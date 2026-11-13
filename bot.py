#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from src.config import Config
from src.formatter import format_reward_list_message, format_reward_notification
from src.lego_client import LegoRewardsClient, RewardLifecycle
from src.storage import RewardEventType, RewardNotificationEvent
from src.telegram_bot import LegoInsidersMonitorBot


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_preview(config: Config, count: int = 5) -> None:
    client = LegoRewardsClient(locale=config.lego_locale)
    now = datetime.now(timezone.utc)
    print(f"Fetching newest LEGO Insiders rewards ({config.lego_locale})...\n")
    items, pagination = client.fetch_rewards_page(page=1, size=24, sort="newest")
    print(f"Total rewards in catalog: {pagination.get('total', len(items))}\n")

    if not items:
        print("No rewards found.")
        return

    # Enrich the #1 newest reward to show exact notification card preview
    top = client.enrich_with_details(items[0])
    sample_event = RewardNotificationEvent(
        event_type=RewardEventType.NEW_REWARD,
        reward=top,
    )
    print("=" * 65)
    print("SAMPLE TELEGRAM NOTIFICATION CARD (Newest Item #1):")
    print("=" * 65)
    print(format_reward_notification(sample_event, tz=config.tz, now=now))
    if top.image_url:
        print(f"🖼 Image URL: {top.image_url}")
    print("=" * 65)
    print()

    upcoming = [r for r in items if r.lifecycle(now) == RewardLifecycle.STARTING_SOON]
    if upcoming:
        print(
            format_reward_list_message(
                f"⏳ Upcoming 'Coming Soon' Rewards ({len(upcoming)})",
                upcoming,
                tz=config.tz,
                now=now,
                limit=10,
            )
        )
        print()

    print(
        format_reward_list_message(
            f"🧱 Top {count} Newest Rewards ({config.lego_locale})",
            items,
            tz=config.tz,
            now=now,
            limit=count,
        )
    )


async def main_async(args: argparse.Namespace) -> int:
    config = Config.from_env()

    if args.preview:
        run_preview(config, count=args.count)
        return 0

    if not config.telegram_bot_token:
        logging.error(
            "TELEGRAM_BOT_TOKEN is not set! Copy .env.example to .env and add your bot token, "
            "or run `python3 bot.py --preview` to test fetching rewards in the terminal."
        )
        return 1

    bot = LegoInsidersMonitorBot(config)

    if args.once:
        await bot.register_commands()
        await bot.process_pending_updates(timeout=1)
        await bot.run_check()
        return 0

    await bot.run_forever()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LEGO Insiders Rewards Telegram Monitor Bot"
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Fetch current LEGO Insiders rewards and print a Telegram notification preview to stdout.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single check and send any pending Telegram notifications, then exit.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Number of newest items to display in --preview mode (default: 5).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    cli_args = parse_args()
    setup_logging(cli_args.verbose)
    try:
        sys.exit(asyncio.run(main_async(cli_args)))
    except KeyboardInterrupt:
        print("\nStopped by user.")
        sys.exit(0)
