"""Portable configuration without provider credentials or hard-coded home paths."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"
USER_ENV = Path.home() / ".config" / "finance-agent" / ".env"


class ConfigError(ValueError):
    """Invalid local configuration."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    currency: str
    timezone: ZoneInfo


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        if key.startswith("FINANCE_"):
            values[key] = value.strip().strip("\"'")
    return values


def load_settings(
    environ: Mapping[str, str] | None = None, env_file: Path = PROJECT_ENV
) -> Settings:
    values = read_env(USER_ENV)
    values.update(read_env(env_file))
    values.update(
        {
            k: v
            for k, v in (os.environ if environ is None else environ).items()
            if k.startswith("FINANCE_")
        }
    )
    data_dir = Path(values.get("FINANCE_DATA_DIR") or "~/.local/share/finance-agent").expanduser()
    if not data_dir.is_absolute():
        raise ConfigError("FINANCE_DATA_DIR must be an absolute path")
    currency = values.get("FINANCE_CURRENCY", "IDR").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ConfigError("FINANCE_CURRENCY must be a three-letter currency code")
    name = values.get("FINANCE_TIMEZONE", "Asia/Jakarta")
    try:
        timezone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown FINANCE_TIMEZONE: {name}") from exc
    return Settings(data_dir, currency, timezone)
