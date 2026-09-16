"""Writes that could not reach doeedd, kept locally until ``doeedd.py flush`` sends them."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .store import save_json


class Outbox:
    """A JSON list of pending writes; each carries its idempotency key so a resend is safe."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.failed_path = path.with_name(path.stem + ".failed.json")

    def entries(self) -> list[dict[str, Any]]:
        """Pending writes, oldest first."""
        if not self.path.is_file():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        return data if isinstance(data, list) else []

    def __len__(self) -> int:
        return len(self.entries())

    def append(self, entry: dict[str, Any]) -> str:
        """Queue a write and return its local id."""
        entry_id = str(uuid.uuid4())
        queued = {**entry, "id": entry_id, "queued_at": datetime.now(UTC).isoformat()}
        save_json(self.path, [*self.entries(), queued])
        return entry_id

    def remove(self, entry_id: str) -> None:
        """Drop a write that has been delivered."""
        save_json(self.path, [e for e in self.entries() if e.get("id") != entry_id])

    def fail(self, entry: dict[str, Any], error: dict[str, Any]) -> None:
        """Move a write doeedd rejected for good (e.g. validation) out of the queue."""
        failed = (
            json.loads(self.failed_path.read_text(encoding="utf-8") or "[]")
            if (self.failed_path.is_file())
            else []
        )
        save_json(self.failed_path, [*failed, {**entry, "error": error}])
        self.remove(entry["id"])
