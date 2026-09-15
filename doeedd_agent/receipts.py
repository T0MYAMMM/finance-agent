"""Receipt uploads to Google Drive, moved from ``finance.py`` (same folders, same file names).

Files land in ``Personal Finance/Receipts/<year>/<MM-Month>/`` named
``YYYY-MM-DD_<merchant>_<amount>_<category>.<ext>``. The Google client comes from Hermes's
``google-workspace`` skill and is imported only when an upload actually happens.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
_SKILL_SCRIPT_DIRS = (
    Path.home() / ".hermes" / "skills" / "productivity" / "google-workspace" / "scripts",
    Path.home()
    / ".hermes"
    / "hermes-agent"
    / "skills"
    / "productivity"
    / "google-workspace"
    / "scripts",
)


class ReceiptUploadError(RuntimeError):
    """The receipt could not be stored in Drive; nothing was recorded."""


def sanitize(text: str | None) -> str:
    """Lower-case, dash-separated, ``a-z0-9`` only (never card numbers or odd characters)."""
    if not text:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower().strip()).strip("-")
    return cleaned or "unknown"


def receipt_filename(
    occurred_on: date, merchant: str | None, amount: int | None, category: str | None, suffix: str
) -> str:
    """``2026-09-10_starbucks_45000_food.jpg``; unknown parts are left out, not invented."""
    parts = [
        occurred_on.isoformat(),
        sanitize(merchant),
        str(amount) if amount else "",
        sanitize(category),
    ]
    extension = suffix.lower().lstrip(".") or "file"
    return "_".join(part for part in parts if part) + f".{extension}"


def drive_link(file_id: str) -> str:
    """Clickable Drive URL for a file id (the id is the canonical key)."""
    return f"https://drive.google.com/file/d/{file_id}/view"


def root_folder_id(config_path: Path = CONFIG_PATH) -> str:
    """The ``Personal Finance`` folder id persisted by ``build_finance_system.py``."""
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))["root_folder_id"]
    except (OSError, KeyError, ValueError) as exc:
        raise ReceiptUploadError(f"root_folder_id missing from {config_path}") from exc


def drive_service() -> Any:
    """A Drive v3 client authorised by Hermes's google-workspace skill."""
    scripts = next((path for path in _SKILL_SCRIPT_DIRS if path.exists()), None)
    if scripts is None:
        raise ReceiptUploadError("google-workspace skill scripts not found")
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        import google_api  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ReceiptUploadError("google-workspace skill could not be imported") from exc
    return google_api.build_service("drive", "v3")


def find_or_create_folder(drive: Any, name: str, parent_id: str) -> str:
    """Reuse a child folder by name, creating it on first use."""
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    query = (
        f"name = '{escaped}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false and '{parent_id}' in parents"
    )
    found = drive.files().list(q=query, spaces="drive", fields="files(id)", pageSize=10).execute()
    files = found.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    return drive.files().create(body=meta, fields="id").execute()["id"]


def upload_receipt(
    path: Path,
    occurred_on: date,
    merchant: str | None,
    amount: int | None,
    category: str | None,
    *,
    drive: Any = None,
    root_id: str | None = None,
) -> dict[str, Any]:
    """Upload a receipt into its month folder and return ``file_id``, ``name``, ``url``."""
    source = path.expanduser()
    if not source.is_file():
        raise ReceiptUploadError(f"receipt file not found: {source.name}")
    try:
        drive = drive or drive_service()
        root = root_id or root_folder_id()
        receipts = find_or_create_folder(drive, "Receipts", root)
        year = find_or_create_folder(drive, str(occurred_on.year), receipts)
        month_name = f"{occurred_on.month:02d}-{occurred_on.strftime('%B')}"
        month = find_or_create_folder(drive, month_name, year)

        from googleapiclient.http import MediaFileUpload  # imported with the Google client

        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        name = receipt_filename(occurred_on, merchant, amount, category, source.suffix)
        media = MediaFileUpload(str(source), mimetype=mime_type, resumable=True)
        created = (
            drive.files()
            .create(body={"name": name, "parents": [month]}, media_body=media, fields="id, name")
            .execute()
        )
    except ReceiptUploadError:
        raise
    except Exception as exc:  # the Google client raises many unrelated types
        log.warning("receipt upload failed: %s", type(exc).__name__)
        raise ReceiptUploadError(f"Drive upload failed: {type(exc).__name__}") from exc

    return {
        "file_id": created["id"],
        "name": created.get("name", name),
        "url": drive_link(created["id"]),
        "mime_type": mime_type,
    }
