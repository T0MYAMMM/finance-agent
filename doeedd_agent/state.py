"""What the agent needs to remember between messages: the last capture and usual accounts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .store import load_json, save_json

_TRAILING_INDEX = re.compile(r":\d+$")


def key_group(key: str | None) -> str | None:
    """Entries logged from one message share a group: ``telegram:1:42:2`` -> ``telegram:1:42``."""
    if not key:
        return None
    return _TRAILING_INDEX.sub("", key)


class StateStore:
    """``state.json``: the last capture (for undo), the last delete (for restore), habits."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        data = load_json(self.path, {})
        return data if isinstance(data, dict) else {}

    def record_capture(self, key: str | None, transaction_id: str) -> None:
        """Remember a new entry; entries from the same message are undone together."""
        data = self._load()
        group = key_group(key)
        last = data.get("last_capture") or {}
        if group and last.get("group") == group:
            ids = [*last.get("ids", []), transaction_id]
        else:
            ids = [transaction_id]
        data["last_capture"] = {
            "group": group,
            "ids": list(dict.fromkeys(ids)),
            "at": datetime.now(UTC).isoformat(),
        }
        save_json(self.path, data)

    def last_capture_ids(self) -> list[str]:
        """Ids of the most recent capture (one message may have produced several)."""
        return list((self._load().get("last_capture") or {}).get("ids", []))

    def record_deleted(self, ids: list[str]) -> None:
        """Remember what undo removed so restore can bring it back."""
        data = self._load()
        data["last_deleted"] = {"ids": ids, "at": datetime.now(UTC).isoformat()}
        data.pop("last_capture", None)
        save_json(self.path, data)

    def take_deleted_ids(self) -> list[str]:
        """Ids removed by the last undo; restoring makes them the last capture again."""
        data = self._load()
        ids = list((data.pop("last_deleted", None) or {}).get("ids", []))
        if ids:
            data["last_capture"] = {"group": None, "ids": ids, "at": datetime.now(UTC).isoformat()}
        save_json(self.path, data)
        return ids

    def remember_account(self, category: str, account: str) -> None:
        """The account last used for a category becomes its default next time."""
        data = self._load()
        data.setdefault("account_by_category", {})[category] = account
        save_json(self.path, data)

    def account_for_category(self, category: str) -> str | None:
        """The account last used with this category, if any."""
        return (self._load().get("account_by_category") or {}).get(category)
