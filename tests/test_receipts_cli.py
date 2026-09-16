import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from doeedd_agent.cli import main
from doeedd_agent.config import load_settings
from doeedd_agent.receipts import drive_link, receipt_filename, sanitize, upload_receipt


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

    info = upload_receipt(
        image, date(2026, 9, 10), "Tomoro Coffee", 27_600, "Food", drive=drive, root_id="root"
    )

    assert drive.file_api.folders == [
        ("Receipts", "root"),
        ("2026", "folder-Receipts"),
        ("09-September", "folder-2026"),
    ]
    assert drive.file_api.uploads[0]["parents"] == ["folder-09-September"]
    assert info == {
        "file_id": "file-1",
        "name": "2026-09-10_tomoro-coffee_27600_food.jpg",
        "url": "https://drive.google.com/file/d/file-1/view",
        "mime_type": "image/jpeg",
    }


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
