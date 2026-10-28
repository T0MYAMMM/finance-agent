from pathlib import Path

import pytest

from finance_agent.config import ConfigError, load_settings


def test_defaults_without_any_env_file() -> None:
    settings = load_settings({})
    assert settings.currency == "IDR"
    assert settings.timezone.key == "Asia/Jakarta"


def test_process_environment_wins_over_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FINANCE_CURRENCY=USD\nFINANCE_TIMEZONE=UTC\nOTHER=ignored\n")
    settings = load_settings({"FINANCE_CURRENCY": "eur"}, env_file)
    assert settings.currency == "EUR"
    assert settings.timezone.key == "UTC"


def test_invalid_timezone_names_the_variable(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="FINANCE_TIMEZONE"):
        load_settings({"FINANCE_TIMEZONE": "Mars/Base"}, tmp_path / "missing.env")
