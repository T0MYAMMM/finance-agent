from datetime import date
from pathlib import Path

import pytest

from doeedd_agent.config import ConfigError, load_settings, read_env_file
from doeedd_agent.formatting import idr, idr_short, percent, short_date


def test_money_and_ratio_formatting() -> None:
    assert idr(45_000) == "Rp45.000"
    assert idr(-1_500) == "-Rp1.500"
    assert idr_short(999_999) == "Rp999.999"
    assert idr_short(1_500_000) == "Rp1,5jt"
    assert idr_short(2_000_000) == "Rp2jt"
    assert idr_short(12_540_000) == "Rp12,5jt"
    assert percent(0.684) == "68%"
    assert percent(None) == "-"
    assert short_date(date(2026, 9, 10)) == "10 Sep"


def test_reads_only_prefixed_values_from_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nTELEGRAM_BOT_TOKEN=secret\nexport DOEEDD_TOKEN='doe_abc'\n"
        'DOEEDD_BASE_URL="https://api.example.com/api/v1/"\nBROKEN LINE\n',
        encoding="utf-8",
    )
    assert read_env_file(env) == {
        "DOEEDD_TOKEN": "doe_abc",
        "DOEEDD_BASE_URL": "https://api.example.com/api/v1/",
    }


def test_settings_from_file_with_environment_override(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("DOEEDD_BASE_URL=https://api.example.com/api/v1/\nDOEEDD_TOKEN=doe_file\n")
    settings = load_settings({"DOEEDD_TOKEN": "doe_env", "DOEEDD_STATE_DIR": str(tmp_path)}, env)
    assert settings.base_url == "https://api.example.com/api/v1"
    assert settings.token == "doe_env"
    assert settings.timezone.key == "Asia/Jakarta"
    assert settings.state_dir == tmp_path


@pytest.mark.parametrize(
    "environ",
    [
        {"DOEEDD_BASE_URL": "https://api.example.com"},
        {"DOEEDD_TOKEN": "doe_x"},
        {"DOEEDD_BASE_URL": "http://api.example.com", "DOEEDD_TOKEN": "doe_x"},
        {
            "DOEEDD_BASE_URL": "https://a.example",
            "DOEEDD_TOKEN": "x",
            "DOEEDD_TIMEZONE": "Mars/Base",
        },
    ],
)
def test_invalid_settings_fail_clearly(environ: dict[str, str], tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_settings(environ, tmp_path / "missing.env")


def test_localhost_may_use_http(tmp_path: Path) -> None:
    settings = load_settings(
        {"DOEEDD_BASE_URL": "http://localhost:3000/api/v1", "DOEEDD_TOKEN": "doe_x"},
        tmp_path / "missing.env",
    )
    assert settings.base_url == "http://localhost:3000/api/v1"


def test_google_ids_are_optional_and_read_from_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "DOEEDD_BASE_URL=https://api.example.com\nDOEEDD_TOKEN=doe_x\n"
        "DOEEDD_DRIVE_ROOT_FOLDER_ID=example-folder-id\n"
    )
    settings = load_settings({"DOEEDD_LEGACY_SHEET_ID": "example_sheet_id"}, env)
    assert settings.drive_root_folder_id == "example-folder-id"
    assert settings.legacy_sheet_id == "example_sheet_id"

    bare = load_settings({"DOEEDD_BASE_URL": "https://a.example", "DOEEDD_TOKEN": "x"}, env)
    assert bare.legacy_sheet_id is None
    without_ids = load_settings(
        {"DOEEDD_BASE_URL": "https://a.example", "DOEEDD_TOKEN": "x"}, tmp_path / "none.env"
    )
    assert without_ids.drive_root_folder_id is None


@pytest.mark.parametrize("name", ["DOEEDD_DRIVE_ROOT_FOLDER_ID", "DOEEDD_LEGACY_SHEET_ID"])
def test_a_url_instead_of_an_id_names_the_variable(name: str, tmp_path: Path) -> None:
    environ = {
        "DOEEDD_BASE_URL": "https://a.example",
        "DOEEDD_TOKEN": "x",
        name: "https://drive.google.com/drive/folders/example",
    }
    with pytest.raises(ConfigError, match=name):
        load_settings(environ, tmp_path / "none.env")
