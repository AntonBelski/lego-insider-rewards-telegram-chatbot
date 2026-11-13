from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import Config
from src.formatter import format_reward_notification
from src.lego_client import RewardItem, RewardLifecycle
from src.storage import RewardEventType, RewardNotificationEvent, StateStore
from src.telegram_bot import compute_next_check_delay_seconds


class TestLegoInsidersBot(unittest.TestCase):
    def setUp(self) -> None:
        self.tz = ZoneInfo("Europe/Warsaw")
        self.now = datetime(2026, 11, 8, 17, 0, 0, tzinfo=timezone.utc)

    def test_reward_lifecycle_and_availability(self) -> None:
        future_start = self.now + timedelta(days=5, hours=3)
        upcoming = RewardItem(
            reward_id="mbtb2fmtjz",
            title="LEGO® Ideas Godzilla Poster",
            point_value=3000,
            start_date=future_start,
            quantity=100,
            reward_type="CODE",
            url_title_slug="lego-ideas-godzilla-poster",
        )
        self.assertEqual(upcoming.lifecycle(self.now), RewardLifecycle.STARTING_SOON)
        self.assertFalse(upcoming.is_available_now(self.now))
        summary = upcoming.availability_summary(self.tz, self.now)
        self.assertIn("Coming Soon", summary)
        self.assertIn("5d 3h", summary)

        # Once start_date has passed
        after_start = future_start + timedelta(minutes=1)
        self.assertEqual(upcoming.lifecycle(after_start), RewardLifecycle.AVAILABLE)
        self.assertTrue(upcoming.is_available_now(after_start))
        self.assertIn("Available Now", upcoming.availability_summary(self.tz, after_start))

        # Out of stock when quantity == 0
        upcoming.quantity = 0
        self.assertFalse(upcoming.is_available_now(after_start))
        self.assertIn("Out of Stock", upcoming.availability_summary(self.tz, after_start))

    def test_state_store_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            store = StateStore(state_path)

            r1 = RewardItem(
                reward_id="r1",
                title="Upcoming Poster",
                point_value=1500,
                start_date=self.now + timedelta(hours=2),
                quantity=50,
            )
            r2 = RewardItem(
                reward_id="r2",
                title="Sold Out Set",
                point_value=2500,
                start_date=self.now - timedelta(days=1),
                quantity=0,
            )

            # First seed does not emit spam events
            events, was_first = store.process_rewards([r1, r2], now=self.now)
            self.assertTrue(was_first)
            self.assertEqual(len(events), 0)

            # Second check: new item r3 added, r1 becomes available, r2 back in stock
            later = self.now + timedelta(hours=3)
            r2.quantity = 25
            r3 = RewardItem(
                reward_id="r3",
                title="Brand New Keychain",
                point_value=800,
                start_date=later - timedelta(minutes=5),
                quantity=100,
            )
            events, was_first = store.process_rewards([r1, r2, r3], now=later)
            self.assertFalse(was_first)
            event_map = {e.reward.reward_id: e.event_type for e in events}
            self.assertEqual(event_map["r1"], RewardEventType.NOW_AVAILABLE)
            self.assertEqual(event_map["r2"], RewardEventType.BACK_IN_STOCK)
            self.assertEqual(event_map["r3"], RewardEventType.NEW_REWARD)

    def test_adaptive_scheduler_delays(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            config = Config(
                telegram_bot_token="dummy",
                telegram_chat_id="12345",
                lego_locale="en-PL",
                timezone_name="Europe/Warsaw",
                poll_interval_minutes=15,
                midnight_boost_interval_seconds=90,
                notify_on_first_run=True,
                state_file=state_path,
            )
            store = StateStore(state_path)

            # 1. Daytime check (18:00 CET) -> 15m (900s)
            daytime = datetime(2026, 11, 8, 17, 0, 0, tzinfo=timezone.utc)
            delay, reason = compute_next_check_delay_seconds(config, store, now=daytime)
            self.assertEqual(delay, 900)
            self.assertIn("Standard Schedule", reason)

            # 2. Midnight CET window (23:02 UTC == 00:02 CET) -> 90s boost
            midnight_cet = datetime(2026, 11, 8, 23, 2, 0, tzinfo=timezone.utc)
            delay, reason = compute_next_check_delay_seconds(config, store, now=midnight_cet)
            self.assertEqual(delay, 90)
            self.assertIn("Midnight Drop Window Boost", reason)

            # 3. Upcoming reward going live in 120 seconds -> wakes up in 125 seconds
            soon_item = RewardItem(
                reward_id="soon1",
                title="Drop in 2 mins",
                point_value=1000,
                start_date=daytime + timedelta(seconds=120),
                quantity=100,
            )
            store.process_rewards([soon_item], now=daytime)
            delay, reason = compute_next_check_delay_seconds(config, store, now=daytime)
            self.assertEqual(delay, 125)
            self.assertIn("Countdown to upcoming release", reason)

    def test_notification_formatter(self) -> None:
        item = RewardItem(
            reward_id="mbtb2fmtjz",
            title="LEGO® Ideas Godzilla Poster",
            point_value=3000,
            start_date=self.now + timedelta(days=2),
            quantity=100,
            category="Art Prints",
            theme="Ideas",
            description="Exclusive collectible poster.",
            lifetime_limit=1,
            skus=[{"sku": "6708298", "skuType": "Global"}],
            url_title_slug="lego-ideas-godzilla-poster",
        )
        ev = RewardNotificationEvent(
            event_type=RewardEventType.NEW_REWARD,
            reward=item,
        )
        msg = format_reward_notification(ev, tz=self.tz, now=self.now)
        self.assertIn("LEGO® Ideas Godzilla Poster", msg)
        self.assertIn("3,000 points", msg)
        self.assertIn("Coming Soon", msg)
        self.assertIn("Exclusive collectible poster.", msg)
        self.assertIn("https://www.lego.com/en-pl/reward/lego-ideas-godzilla-poster-mbtb2fmtjz", msg)


if __name__ == "__main__":
    unittest.main()
