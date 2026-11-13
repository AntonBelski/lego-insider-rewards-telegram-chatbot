from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.lego_client import RewardItem, RewardLifecycle
from src.storage import RewardEventType, RewardNotificationEvent


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"</p\s*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return html.unescape(cleaned).strip()


def format_points(points: int) -> str:
    if points == 0:
        return "0 points (Free)"
    return f"{points:,} points"


def format_reward_notification(
    event: RewardNotificationEvent,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    r = event.reward
    stage = r.lifecycle(now)

    if event.event_type == RewardEventType.NEW_REWARD:
        if stage == RewardLifecycle.STARTING_SOON:
            header = "🆕⏳ <b>NEW UPCOMING LEGO® INSIDERS REWARD</b>"
        else:
            header = "🆕🧱 <b>NEW LEGO® INSIDERS REWARD ADDED!</b>"
    elif event.event_type == RewardEventType.NOW_AVAILABLE:
        header = "🟢🎉 <b>REWARD IS NOW AVAILABLE TO REDEEM!</b>"
    elif event.event_type == RewardEventType.BACK_IN_STOCK:
        header = "🔄🟢 <b>REWARD IS BACK IN STOCK!</b>"
    else:
        header = "🧱 <b>LEGO® INSIDERS REWARD UPDATE</b>"

    lines: list[str] = [
        header,
        "",
        f"🏷 <b>{html.escape(r.title)}</b>",
        f"💎 <b>Points:</b> {html.escape(format_points(r.point_value))}",
        f"📦 <b>Availability:</b> {html.escape(r.availability_summary(tz, now))}",
        f"🔖 <b>Type:</b> {html.escape(r.human_type)}",
    ]

    meta_parts: list[str] = []
    if r.category:
        meta_parts.append(f"Category: {html.escape(_strip_html(r.category))}")
    if r.theme:
        meta_parts.append(f"Theme: {html.escape(_strip_html(r.theme))}")
    if meta_parts:
        lines.append(f"🗂 <b>{' | '.join(meta_parts)}</b>")

    if r.start_date:
        local_start = r.start_date.astimezone(tz)
        label = "Release Date" if stage == RewardLifecycle.STARTING_SOON else "Added / Live Since"
        lines.append(
            f"🗓 <b>{label}:</b> {html.escape(local_start.strftime('%Y-%m-%d %H:%M %Z'))}"
        )

    if r.end_date:
        local_end = r.end_date.astimezone(tz)
        lines.append(
            f"⏰ <b>Valid Until:</b> {html.escape(local_end.strftime('%Y-%m-%d %H:%M %Z'))}"
        )

    extra_details: list[str] = []
    if r.lifetime_limit:
        extra_details.append(f"Limit: {r.lifetime_limit}/account")
    if r.primary_sku:
        extra_details.append(f"SKU: {r.primary_sku}")
    if extra_details:
        lines.append(f"ℹ️ <i>{html.escape(' • '.join(extra_details))}</i>")

    desc = _strip_html(r.description)
    if desc:
        if len(desc) > 450:
            desc = desc[:447] + "..."
        lines.extend(["", f"📝 <b>Description:</b>\n{html.escape(desc)}"])

    if r.overview_note:
        note = _strip_html(r.overview_note)
        if note:
            lines.extend(["", f"📌 <i>{html.escape(note)}</i>"])

    lines.extend(
        [
            "",
            f"🔗 <a href=\"{html.escape(r.url)}\">Open Reward on LEGO.com</a>",
        ]
    )

    return "\n".join(lines)


def format_reward_list_message(
    title: str,
    rewards: list[RewardItem],
    tz: ZoneInfo,
    now: datetime | None = None,
    limit: int = 10,
) -> str:
    now = now or datetime.now(timezone.utc)
    if not rewards:
        return f"<b>{html.escape(title)}</b>\n\nNo matching rewards found."

    lines: list[str] = [f"<b>{html.escape(title)}</b>", ""]
    for idx, r in enumerate(rewards[:limit], start=1):
        avail = r.availability_summary(tz, now)
        cat_info = f" ({html.escape(_strip_html(r.category))})" if r.category else ""
        lines.append(
            f"{idx}. <a href=\"{html.escape(r.url)}\"><b>{html.escape(r.title)}</b></a>{cat_info}\n"
            f"   💎 <b>{html.escape(format_points(r.point_value))}</b> • {html.escape(avail)}"
        )
    return "\n".join(lines)
