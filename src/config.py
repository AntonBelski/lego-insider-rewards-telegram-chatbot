from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_dotenv(dotenv_path: Path) -> None:
    """Lightweight .env file loader (no external dependency required)."""
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: str | None
    lego_locale: str
    timezone_name: str
    poll_interval_minutes: int
    midnight_boost_interval_seconds: int
    notify_on_first_run: bool
    state_file: Path

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def rewards_page_url(self) -> str:
        return f"https://www.lego.com/{self.lego_locale.lower()}/insiders/rewards?r-sort=newest"

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> Config:
        project_root = Path(__file__).resolve().parent.parent
        _load_dotenv(env_path or (project_root / ".env"))

        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None
        locale = os.getenv("LEGO_LOCALE", "en-PL").strip() or "en-PL"
        tz_name = os.getenv("TIMEZONE", "Europe/Warsaw").strip() or "Europe/Warsaw"
        poll_minutes = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
        boost_seconds = int(os.getenv("MIDNIGHT_BOOST_INTERVAL_SECONDS", "90"))
        notify_first = os.getenv("NOTIFY_ON_FIRST_RUN", "true").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        state_file = Path(
            os.getenv("STATE_FILE", str(project_root / "data" / "rewards_state.json"))
        )

        return cls(
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            lego_locale=locale,
            timezone_name=tz_name,
            poll_interval_minutes=max(1, poll_minutes),
            midnight_boost_interval_seconds=max(30, boost_seconds),
            notify_on_first_run=notify_first,
            state_file=state_file,
        )
