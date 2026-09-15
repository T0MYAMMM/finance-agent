"""What the assistant has already said proactively, so it never repeats itself or nags."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .store import load_json, save_json

LEVELS = {"warn": 1, "over": 2}
KEEP_SENT_DAYS = 62
KEEP_ALERT_MONTHS = 3


class NotifyState:
    """``notify_state.json``: messages sent per day, budget alerts per month, snooze."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        data = load_json(self.path, {})
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any], today: date) -> None:
        oldest_day = (today - timedelta(days=KEEP_SENT_DAYS)).isoformat()
        data["sent"] = {
            day: kinds for day, kinds in data.get("sent", {}).items() if day >= oldest_day
        }
        months = sorted(data.get("alerted", {}))[-KEEP_ALERT_MONTHS:]
        data["alerted"] = {month: data["alerted"][month] for month in months}
        save_json(self.path, data)

    def sent_on(self, day: date) -> list[str]:
        """Kinds of scheduled messages sent on ``day``."""
        return list(self._load().get("sent", {}).get(day.isoformat(), []))

    def sent_in_month(self, kind: str, month: str) -> bool:
        """Whether a message of ``kind`` went out during ``month`` (``YYYY-MM``)."""
        sent = self._load().get("sent", {})
        return any(day.startswith(month) and kind in kinds for day, kinds in sent.items())

    def record_sent(self, day: date, kind: str) -> None:
        """Count a scheduled message against the day's budget."""
        data = self._load()
        data.setdefault("sent", {}).setdefault(day.isoformat(), []).append(kind)
        self._save(data, day)

    def alert_level(self, month: str, category: str) -> str | None:
        """The highest budget level already reported for a category this month."""
        return self._load().get("alerted", {}).get(month, {}).get(category)

    def mark_alerted(self, month: str, category: str, level: str, today: date) -> None:
        """Remember a warn/over report; a lower level never replaces a higher one."""
        data = self._load()
        month_alerts = data.setdefault("alerted", {}).setdefault(month, {})
        current = month_alerts.get(category)
        if current is None or LEVELS.get(level, 0) > LEVELS.get(current, 0):
            month_alerts[category] = level
        self._save(data, today)

    def snoozed_until(self) -> date | None:
        """Last day proactive messages are paused, if any."""
        value = self._load().get("snoozed_until")
        return date.fromisoformat(value) if value else None

    def snooze(self, until: date | None, today: date) -> None:
        """Pause proactive messages through ``until`` (``None`` resumes them)."""
        data = self._load()
        if until is None:
            data.pop("snoozed_until", None)
        else:
            data["snoozed_until"] = until.isoformat()
        self._save(data, today)
