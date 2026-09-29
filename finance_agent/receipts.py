"""Local receipt archive; replace this adapter to use cloud storage."""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import date
from pathlib import Path

from .domain import InputError


def safe_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "receipt"


def archive(
    path: Path, root: Path, occurred_on: str, merchant: str, amount: str
) -> tuple[str, str]:
    source = path.expanduser().resolve()
    if not source.is_file():
        raise InputError(f"Receipt file not found: {path}")
    if source.stat().st_size > 25 * 1024 * 1024:
        raise InputError("Receipt is larger than 25 MB")
    suffix = source.suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic"}:
        raise InputError("Receipt must be PDF or an image")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    day = date.fromisoformat(occurred_on)
    folder = root / "receipts" / str(day.year) / f"{day.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{occurred_on}_{safe_name(merchant)}_{safe_name(amount)}_{digest[:12]}{suffix}"
    target = folder / filename
    if not target.exists():
        shutil.copy2(source, target)
        target.chmod(0o600)
    return digest, str(target)
