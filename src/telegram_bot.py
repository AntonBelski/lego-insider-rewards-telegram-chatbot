from __future__ import annotations

import asyncio
import html
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from src.config import Config
from src.formatter import format_reward_list_message, format_reward_notification
from src.lego_client import LegoRewardsClient, RewardItem, RewardLifecycle
from src.storage import RewardEventType, RewardNotificationEvent, StateStore

logger = logging.getLogger(__name__)


def compute_next_check_delay_seconds(
    config: Config,
    store: StateStore,
    now: datetime | None = None,
) -> tuple[int, str]:
    """
    Computes how many seconds to wait until the next check, along with a human reason:
    1. If a tracked STARTING_SOON reward goes live sooner than the standard interval,
       wake up 5 seconds after its start_date.
    2. During the LEGO midnight release window in Europe/Warsaw (23:55-00:30 and 00:55-01:15),
       use the boosted interval (default 90s) because physical LEGO Insiders rewards drop at 00:00 CET/CEST.
    3. Otherwise use the standard interval (default 15 minutes).
    """
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(config.tz)

    in_midnight_window = (
        (local_now.hour == 23 and local_now.minute >= 55)
        or (local_now.hour == 0 and local_now.minute <= 30)
        or (local_now.hour == 0 and local_now.minute >= 55)
        or (local_now.hour == 1 and local_now.minute <= 15)
    )

    if in_midnight_window:
        delay = config.midnight_boost_interval_seconds
        reason = f"Midnight Drop Window Boost (every {delay}s)"
    else:
        delay = config.poll_interval_minutes * 60
        reason = f"Standard Schedule (every {config.poll_interval_minutes}m)"

    next_start = store.next_starting_soon_datetime(now)
    if next_start is not None:
        seconds_until_live = int((next_start - now).total_seconds()) + 5
        if 5 <= seconds_until_live < delay:
            local_start = next_start.astimezone(config.tz).strftime("%Y-%m-%d %H:%M %Z")
            return seconds_until_live, f"Countdown to upcoming release at {local_start}"

    return delay, reason


class TelegramAPI:
    """Minimal, dependency-free Telegram Bot HTTP API client."""

    def __init__(self, token: str, timeout: int = 35) -> None:
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def _call_sync(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{method}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if not body.get("ok"):
                raise RuntimeError(f"Telegram API error ({method}): {body}")
            return body

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._call_sync, method, payload)

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        disable_preview: bool = False,
    ) -> None:
        await self.call(
            "sendMessage",
            {
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": disable_preview,
            },
        )

    async def send_photo_or_message(
        self,
        chat_id: str | int,
        text: str,
        photo_url: str | None = None,
    ) -> None:
        if photo_url and len(text) <= 1024:
            try:
                await self.call(
                    "sendPhoto",
                    {
                        "chat_id": str(chat_id),
                        "photo": photo_url,
                        "caption": text,
                        "parse_mode": "HTML",
                    },
                )
                return
            except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
                logger.warning(
                    "sendPhoto failed for %s (%s), falling back to sendMessage",
                    photo_url,
                    exc,
                )
        await self.send_message(chat_id, text, disable_preview=False)


class LegoInsidersMonitorBot:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.lego = LegoRewardsClient(locale=config.lego_locale)
        self.store = StateStore(config.state_file)
        self.tg = (
            TelegramAPI(config.telegram_bot_token)
            if config.telegram_bot_token
            else None
        )
        self._update_offset: int = self.store.last_update_id
        self._check_lock = asyncio.Lock()

    def _target_chat_ids(self) -> list[str]:
        targets = set(self.store.chat_ids)
        if self.config.telegram_chat_id:
            targets.add(str(self.config.telegram_chat_id).strip())
        return sorted(c for c in targets if c)

    async def register_commands(self) -> None:
        if not self.tg:
            return
        commands = [
            {"command": "latest", "description": "Show the newest LEGO Insiders rewards"},
            {"command": "upcoming", "description": "Show 'Coming Soon' rewards & release dates"},
            {"command": "detail", "description": "Show full card for reward #N (e.g. /detail 1)"},
            {"command": "check", "description": "Force an immediate check for new rewards"},
            {"command": "status", "description": "Show bot status and polling schedule"},
            {"command": "help", "description": "Show help and available commands"},
        ]
        try:
            await self.tg.call("setMyCommands", {"commands": commands})
        except Exception as exc:
            logger.warning("Could not set bot commands: %s", exc)

    def _is_authorized_chat(self, chat_id: str | int) -> bool:
        if not self.config.telegram_chat_id:
            return True
        return str(chat_id) == str(self.config.telegram_chat_id)

    async def run_check(
        self, notify_Targets: list[str] | None = None
    ) -> list[RewardNotificationEvent]:
        async with self._check_lock:
            now = datetime.now(timezone.utc)
            if not self.store.initialized:
                logger.info("Performing initial full catalog sync from LEGO (%s)...", self.config.lego_locale)
                items = await asyncio.to_thread(self.lego.fetch_all_rewards)
            else:
                logger.info("Checking newest LEGO Insiders rewards (%s)...", self.config.lego_locale)
                page_items, _ = await asyncio.to_thread(
                    self.lego.fetch_rewards_page, 1, 24, "newest"
                )
                items = page_items

            events, was_first_seed = self.store.process_rewards(items, now=now)

            # On first run, if notify_on_first_run is enabled and there are chat_ids,
            # send a welcome summary + the #1 newest reward card so the user sees it working immediately.
            target_chats = notify_Targets or self._target_chat_ids()

            if was_first_seed and self.config.notify_on_first_run and items and target_chats and self.tg:
                newest = await asyncio.to_thread(self.lego.enrich_with_details, items[0])
                upcoming = [
                    r for r in items if r.lifecycle(now) == RewardLifecycle.STARTING_SOON
                ]
                intro_lines = [
                    "✅ <b>LEGO® Insiders Rewards Monitor Initialized!</b>",
                    f"🌍 <b>Region:</b> <code>{html.escape(self.config.lego_locale)}</code>",
                    f"📦 <b>Catalog Seeded:</b> {len(items)} rewards tracked ({len(upcoming)} Coming Soon)",
                    "",
                    "Below is the latest reward currently listed on LEGO Insiders:",
                ]
                sample_event = RewardNotificationEvent(
                    event_type=RewardEventType.NEW_REWARD,
                    reward=newest,
                )
                card_text = format_reward_notification(
                    sample_event, tz=self.config.tz, now=now
                )
                for cid in target_chats:
                    try:
                        await self.tg.send_message(cid, "\n".join(intro_lines), disable_preview=True)
                        await self.tg.send_photo_or_message(
                            cid, card_text, photo_url=newest.image_url
                        )
                    except Exception as exc:
                        logger.error("Failed to send initial notification to %s: %s", cid, exc)

            # Send notifications for all detected events
            for event in events:
                event.reward = await asyncio.to_thread(
                    self.lego.enrich_with_details, event.reward
                )
                msg = format_reward_notification(event, tz=self.config.tz, now=now)
                logger.info(
                    "Detected %s: %s (%d pts)",
                    event.event_type.value,
                    event.reward.title,
                    event.reward.point_value,
                )
                if self.tg and target_chats:
                    for cid in target_chats:
                        try:
                            await self.tg.send_photo_or_message(
                                cid, msg, photo_url=event.reward.image_url
                            )
                        except Exception as exc:
                            logger.error("Failed sending notification to %s: %s", cid, exc)

            return events

    async def handle_command(self, chat_id: str, text: str) -> None:
        if not self.tg:
            return
        if not self._is_authorized_chat(chat_id):
            await self.tg.send_message(
                chat_id,
                "⛔ Unauthorized chat. Please check your <code>TELEGRAM_CHAT_ID</code> configuration.",
            )
            return

        parts = text.strip().split()
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        if cmd in ("/start", "/help"):
            added = self.store.add_chat_id(chat_id)
            sub_status = (
                "✅ Your chat has been subscribed to automatic reward alerts!"
                if added
                else "✅ Your chat is subscribed to automatic reward alerts."
            )
            help_msg = (
                "🧱 <b>LEGO® Insiders Rewards Bot</b>\n\n"
                f"{sub_status}\n"
                f"🆔 <b>Your Telegram Chat ID:</b> <code>{html.escape(str(chat_id))}</code>\n"
                f"🌍 <b>Monitored Page:</b> <a href=\"{html.escape(self.config.rewards_page_url)}\">{html.escape(self.config.lego_locale)} Newest Rewards</a>\n\n"
                "<b>Available Commands:</b>\n"
                "• /latest — Show the newest rewards & points\n"
                "• /upcoming — Show 'Coming Soon' rewards & exact drop dates\n"
                "• /detail <code>1</code> — Full card + description for reward #1\n"
                "• /check — Check LEGO.com right now\n"
                "• /status — Bot schedule & catalog statistics\n"
                "• /help — Show this message"
            )
            await self.tg.send_message(chat_id, help_msg, disable_preview=True)

        elif cmd == "/latest":
            limit = 8
            if arg and arg.isdigit():
                limit = max(1, min(15, int(arg)))
            items, _ = await asyncio.to_thread(
                self.lego.fetch_rewards_page, 1, 24, "newest"
            )
            msg = format_reward_list_message(
                f"🧱 Newest LEGO® Insiders Rewards ({self.config.lego_locale})",
                items,
                tz=self.config.tz,
                limit=limit,
            )
            msg += "\n\n💡 <i>Tip: Send</i> <code>/detail 1</code> <i>for full description & photo of item #1.</i>"
            await self.tg.send_message(chat_id, msg, disable_preview=True)

        elif cmd == "/upcoming":
            items = await asyncio.to_thread(self.lego.fetch_all_rewards)
            now = datetime.now(timezone.utc)
            upcoming = [
                r for r in items if r.lifecycle(now) == RewardLifecycle.STARTING_SOON
            ]
            upcoming.sort(
                key=lambda r: r.start_date or datetime.max.replace(tzinfo=timezone.utc)
            )
            if not upcoming:
                await self.tg.send_message(
                    chat_id,
                    "⏳ <b>Upcoming LEGO® Insiders Rewards</b>\n\n"
                    "There are currently no rewards marked as <i>Coming Soon</i>.",
                )
            else:
                msg = format_reward_list_message(
                    f"⏳ Upcoming 'Coming Soon' Rewards ({len(upcoming)})",
                    upcoming,
                    tz=self.config.tz,
                    now=now,
                    limit=15,
                )
                await self.tg.send_message(chat_id, msg, disable_preview=True)

        elif cmd == "/detail":
            idx = 1
            if arg and arg.isdigit():
                idx = max(1, int(arg))
            items, _ = await asyncio.to_thread(
                self.lego.fetch_rewards_page, 1, 24, "newest"
            )
            if not items or idx > len(items):
                await self.tg.send_message(
                    chat_id,
                    f"⚠️ Please choose a number between 1 and {len(items)}.",
                )
                return
            chosen = await asyncio.to_thread(
                self.lego.enrich_with_details, items[idx - 1]
            )
            ev = RewardNotificationEvent(
                event_type=RewardEventType.NEW_REWARD, reward=chosen
            )
            card = format_reward_notification(ev, tz=self.config.tz)
            await self.tg.send_photo_or_message(
                chat_id, card, photo_url=chosen.image_url
            )

        elif cmd == "/check":
            await self.tg.send_message(chat_id, "🔍 Checking LEGO® Insiders Rewards now...")
            events = await self.run_check(notify_Targets=[chat_id])
            if not events:
                await self.tg.send_message(
                    chat_id,
                    f"✅ No new rewards or availability changes detected.\n"
                    f"Currently tracking <b>{len(self.store.rewards)}</b> rewards.",
                )

        elif cmd == "/status":
            now = datetime.now(timezone.utc)
            delay, reason = compute_next_check_delay_seconds(
                self.config, self.store, now=now
            )
            last_check = "Never"
            if self.store.last_checked_at:
                dt = datetime.fromisoformat(self.store.last_checked_at).astimezone(
                    self.config.tz
                )
                last_check = dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            next_start = self.store.next_starting_soon_datetime(now)
            next_drop_str = (
                next_start.astimezone(self.config.tz).strftime("%Y-%m-%d %H:%M %Z")
                if next_start
                else "None currently scheduled"
            )
            status_msg = (
                "📊 <b>LEGO® Insiders Bot Status</b>\n\n"
                f"• <b>Locale:</b> <code>{html.escape(self.config.lego_locale)}</code>\n"
                f"• <b>Timezone:</b> <code>{html.escape(self.config.timezone_name)}</code>\n"
                f"• <b>Tracked Rewards:</b> {len(self.store.rewards)}\n"
                f"• <b>Subscribed Chats:</b> {len(self._target_chat_ids())}\n"
                f"• <b>Last Checked:</b> {html.escape(last_check)}\n"
                f"• <b>Next Scheduled Drop:</b> {html.escape(next_drop_str)}\n"
                f"• <b>Active Schedule Mode:</b> {html.escape(reason)} (next check in ~{delay}s)"
            )
            await self.tg.send_message(chat_id, status_msg, disable_preview=True)

    async def process_pending_updates(self, timeout: int = 1) -> None:
        """Processes any queued Telegram commands once (useful for GitHub Actions --once mode)."""
        if not self.tg:
            return
        try:
            resp = await self.tg.call(
                "getUpdates",
                {
                    "offset": self._update_offset,
                    "timeout": timeout,
                    "allowed_updates": ["message"],
                },
            )
            for upd in resp.get("result") or []:
                self._update_offset = max(
                    self._update_offset, int(upd["update_id"]) + 1
                )
                self.store.set_last_update_id(self._update_offset)
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id") or "")
                if chat_id and text.startswith("/"):
                    await self.handle_command(chat_id, text)
        except Exception as exc:
            logger.warning("Could not process pending Telegram updates: %s", exc)

    async def poll_telegram_updates_loop(self) -> None:
        if not self.tg:
            return
        await self.register_commands()
        logger.info("Started Telegram command listener loop.")
        while True:
            try:
                await self.process_pending_updates(timeout=25)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram getUpdates error (retrying in 5s): %s", exc)
                await asyncio.sleep(5)

    async def monitor_rewards_loop(self) -> None:
        logger.info(
            "Starting LEGO Insiders Rewards monitor loop for %s...",
            self.config.rewards_page_url,
        )
        while True:
            try:
                await self.run_check()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Error during LEGO rewards check: %s", exc)

            delay, reason = compute_next_check_delay_seconds(self.config, self.store)
            logger.info("Next check in %ds (%s)", delay, reason)
            await asyncio.sleep(delay)

    async def run_forever(self) -> None:
        await asyncio.gather(
            self.monitor_rewards_loop(),
            self.poll_telegram_updates_loop(),
        )
