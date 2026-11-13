from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from src.lego_client import RewardItem, RewardLifecycle, parse_iso_datetime

logger = logging.getLogger(__name__)


class RewardEventType(str, Enum):
    NEW_REWARD = "NEW_REWARD"
    NOW_AVAILABLE = "NOW_AVAILABLE"
    BACK_IN_STOCK = "BACK_IN_STOCK"


@dataclass
class RewardNotificationEvent:
    event_type: RewardEventType
    reward: RewardItem
    previous_lifecycle: str | None = None


class StateStore:
    """Persists tracked LEGO rewards and subscribed Telegram chat IDs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rewards: dict[str, dict[str, Any]] = {}
        self.chat_ids: set[str] = set()
        self.last_update_id: int = 0
        self.last_checked_at: str | None = None
        self.initialized: bool = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.rewards = raw.get("rewards") or {}
            self.chat_ids = set(str(cid) for cid in (raw.get("chat_ids") or []) if cid)
            self.last_update_id = int(raw.get("last_update_id") or 0)
            self.last_checked_at = raw.get("last_checked_at")
            self.initialized = bool(raw.get("initialized", bool(self.rewards)))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Failed to load state file %s: %s", self.path, exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "initialized": self.initialized,
            "last_checked_at": self.last_checked_at,
            "last_update_id": self.last_update_id,
            "chat_ids": sorted(self.chat_ids),
            "rewards": self.rewards,
        }
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp_path.replace(self.path)

    def add_chat_id(self, chat_id: str | int) -> bool:
        cid = str(chat_id).strip()
        if not cid or cid in self.chat_ids:
            return False
        self.chat_ids.add(cid)
        self.save()
        return True

    def set_last_update_id(self, update_id: int) -> None:
        if update_id > self.last_update_id:
            self.last_update_id = update_id
            self.save()

    def _serialize_reward(
        self, item: RewardItem, now: datetime
    ) -> dict[str, Any]:
        stage = item.lifecycle(now).value
        existing = self.rewards.get(item.reward_id) or {}
        return {
            "reward_id": item.reward_id,
            "title": item.title,
            "point_value": item.point_value,
            "lifecycle": stage,
            "in_stock": item.in_stock,
            "quantity": item.quantity,
            "start_date": item.start_date.isoformat() if item.start_date else None,
            "end_date": item.end_date.isoformat() if item.end_date else None,
            "url": item.url,
            "first_seen_at": existing.get("first_seen_at", now.isoformat()),
            "updated_at": now.isoformat(),
        }

    @staticmethod
    def _has_meaningful_change(
        old_entry: dict[str, Any] | None, new_entry: dict[str, Any]
    ) -> bool:
        if old_entry is None:
            return True
        keys = (
            "title",
            "point_value",
            "lifecycle",
            "in_stock",
            "start_date",
            "end_date",
            "url",
        )
        return any(old_entry.get(k) != new_entry.get(k) for k in keys)

    def process_rewards(
        self, items: list[RewardItem], now: datetime | None = None
    ) -> tuple[list[RewardNotificationEvent], bool]:
        """
        Compares fetched rewards against stored state and returns events.
        Only writes to disk when the catalog state meaningfully changes (or on first seed),
        avoiding noisy git commits on every scheduled GitHub Actions run.
        Returns (events, was_first_seed).
        """
        now = now or datetime.now(timezone.utc)
        was_first_seed = not self.initialized
        events: list[RewardNotificationEvent] = []
        state_changed = was_first_seed

        for item in items:
            current_stage = item.lifecycle(now)
            current_in_stock = item.in_stock
            prev = self.rewards.get(item.reward_id)

            if prev is None:
                if not was_first_seed:
                    events.append(
                        RewardNotificationEvent(
                            event_type=RewardEventType.NEW_REWARD,
                            reward=item,
                        )
                    )
            else:
                prev_stage = prev.get("lifecycle")
                prev_in_stock = prev.get("in_stock", True)

                # Transition from STARTING_SOON -> AVAILABLE / ENDING_SOON
                if (
                    prev_stage == RewardLifecycle.STARTING_SOON.value
                    and current_stage
                    in (RewardLifecycle.AVAILABLE, RewardLifecycle.ENDING_SOON)
                    and current_in_stock
                ):
                    events.append(
                        RewardNotificationEvent(
                            event_type=RewardEventType.NOW_AVAILABLE,
                            reward=item,
                            previous_lifecycle=prev_stage,
                        )
                    )
                # Transition from Out of Stock -> Back in Stock
                elif (
                    prev_in_stock is False
                    and current_in_stock is True
                    and current_stage
                    in (RewardLifecycle.AVAILABLE, RewardLifecycle.ENDING_SOON)
                ):
                    events.append(
                        RewardNotificationEvent(
                            event_type=RewardEventType.BACK_IN_STOCK,
                            reward=item,
                            previous_lifecycle=prev_stage,
                        )
                    )

            new_entry = self._serialize_reward(item, now)
            if self._has_meaningful_change(prev, new_entry):
                self.rewards[item.reward_id] = new_entry
                state_changed = True

        self.initialized = True
        if state_changed:
            self.last_checked_at = now.isoformat()
            self.save()
        return events, was_first_seed

    def next_starting_soon_datetime(
        self, now: datetime | None = None
    ) -> datetime | None:
        """Returns the earliest upcoming start_date among tracked STARTING_SOON rewards."""
        now = now or datetime.now(timezone.utc)
        upcoming: list[datetime] = []
        for entry in self.rewards.values():
            dt = parse_iso_datetime(entry.get("start_date"))
            if dt and dt > now:
                upcoming.append(dt)
        return min(upcoming) if upcoming else None
