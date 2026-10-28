import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from doeedd_agent.cli import main, open_app
from doeedd_agent.config import Settings, load_settings
from doeedd_agent.receipts import (
    ReceiptUploadError,
    drive_link,
    receipt_filename,
    resumable_media,
    sanitize,
    upload_receipt,
)


def test_receipt_names_follow_the_existing_convention() -> None:
    assert sanitize("Food & Dining") == "food-dining"
    assert sanitize("") == ""
    name = receipt_filename(date(2026, 9, 10), "Tomoro Coffee", 27_600, "Food", ".JPG")
    assert name == "2026-09-10_tomoro-coffee_27600_food.jpg"
    assert receipt_filename(date(2026, 9, 10), None, None, None, "") == "2026-09-10.file"
    assert drive_link("abc") == "https://drive.google.com/file/d/abc/view"


class FakeRequest:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def execute(self) -> dict[str, Any]:
        return self.result


class FakeFiles:
    def __init__(self) -> None:
        self.folders: list[tuple[str, str]] = []
        self.uploads: list[dict[str, Any]] = []

    def list(self, **_: Any) -> FakeRequest:
        return FakeRequest({"files": []})

    def create(self, body: dict[str, Any], **kwargs: Any) -> FakeRequest:
        if "media_body" in kwargs:
            self.uploads.append(body)
            return FakeRequest({"id": "file-1", "name": body["name"]})
        self.folders.append((body["name"], body["parents"][0]))
        return FakeRequest({"id": f"folder-{body['name']}"})


class FakeDrive:
    def __init__(self) -> None:
        self.file_api = FakeFiles()

    def files(self) -> FakeFiles:
        return self.file_api


def test_upload_routes_into_year_and_month_folders(tmp_path: Path) -> None:
    image = tmp_path / "IMG_1.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    drive = FakeDrive()
    media_calls: list[tuple[Path, str]] = []

    def fake_media(path: Path, mime_type: str) -> str:
        media_calls.append((path, mime_type))
        return "media-body"

    info = upload_receipt(
        image,
        date(2026, 9, 10),
        "Tomoro Coffee",
        27_600,
        "Food",
        drive=drive,
        root_id="root",
        media=fake_media,
    )

    assert drive.file_api.folders == [
        ("Receipts", "root"),
        ("2026", "folder-Receipts"),
        ("09-September", "folder-2026"),
    ]
    assert drive.file_api.uploads[0]["parents"] == ["folder-09-September"]
    assert media_calls == [(image, "image/jpeg")]
    assert info == {
        "file_id": "file-1",
        "name": "2026-09-10_tomoro-coffee_27600_food.jpg",
        "url": "https://drive.google.com/file/d/file-1/view",
        "mime_type": "image/jpeg",
    }


def test_missing_google_client_is_a_clear_upload_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "googleapiclient.http", None)  # import now fails
    with pytest.raises(ReceiptUploadError, match="'drive' extra"):
        resumable_media(tmp_path / "r.jpg", "image/jpeg")


def test_upload_without_a_root_folder_names_the_variable(tmp_path: Path) -> None:
    image = tmp_path / "IMG_1.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    drive = FakeDrive()
    with pytest.raises(ReceiptUploadError, match="DOEEDD_DRIVE_ROOT_FOLDER_ID"):
        upload_receipt(image, date(2026, 9, 10), None, 1_000, None, drive=drive, root_id=None)
    assert drive.file_api.folders == []


def offline_settings(tmp_path: Path, **environ: str) -> Settings:
    """Settings for a doeedd that is never contacted."""
    base = {
        "DOEEDD_BASE_URL": "https://doeedd.example",
        "DOEEDD_TOKEN": "doe_test",
        "DOEEDD_STATE_DIR": str(tmp_path / "state"),
    }
    return load_settings(base | environ, tmp_path / "none.env")


def test_cli_uploader_uses_the_configured_root_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("doeedd_agent.cli.load_settings", lambda: offline_settings(tmp_path))
    app = open_app(argparse.Namespace())
    try:
        image = tmp_path / "IMG_1.jpg"
        image.write_bytes(b"\xff\xd8\xff")
        assert app.recorder.uploader is not None
        with pytest.raises(ReceiptUploadError, match="DOEEDD_DRIVE_ROOT_FOLDER_ID"):
            app.recorder.uploader(image, date(2026, 9, 10), None, 1_000, None)
    finally:
        app.client.close()


def test_migrate_sheets_without_a_sheet_id_names_the_variable(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("doeedd_agent.cli.load_settings", lambda: offline_settings(tmp_path))
    assert main(["--json", "migrate-sheets"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert "DOEEDD_LEGACY_SHEET_ID" in result["reply"]


def test_cli_parse_commands_work_offline(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "parse", "amount", "1,5jt"]) == 0
    assert json.loads(capsys.readouterr().out)["value"] == 1_500_000

    assert main(["--json", "--today", "2026-09-15", "parse", "date", "kemarin"]) == 0
    assert json.loads(capsys.readouterr().out)["value"] == "2026-09-14"

    assert main(["--json", "parse", "amount", "45.5"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["status"] == "error"
    assert error["kind"] == "ambiguous"


def test_cli_reports_missing_configuration(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "doeedd_agent.cli.load_settings", lambda: load_settings({}, tmp_path / "none.env")
    )
    assert main(["--json", "health"]) == 2
    assert "Missing" in json.loads(capsys.readouterr().out)["reply"]
