"""Runtime configuration, read from the environment or the Hermes ``.env`` file."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HERMES_ENV_FILE = Path.home() / ".hermes" / ".env"
DEFAULT_STATE_DIR = Path.home() / ".hermes" / "doeedd"
DEFAULT_TIMEZONE = "Asia/Jakarta"
ENV_PREFIX = "DOEEDD_"
LOCAL_URL_PREFIXES = ("http://localhost", "http://127.0.0.1")


class ConfigError(RuntimeError):
    """The agent cannot run because configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Everything the CLI needs to reach doeedd and keep its local state."""

    base_url: str
    token: str
    timezone: ZoneInfo
    state_dir: Path


def read_env_file(path: Path, prefix: str = ENV_PREFIX) -> dict[str, str]:
    """Return the ``KEY=VALUE`` pairs whose key starts with ``prefix`` from a dotenv file."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        if key.startswith(prefix):
            values[key] = value.strip().strip("'\"")
    return values


def load_settings(
    environ: Mapping[str, str] | None = None, env_file: Path = HERMES_ENV_FILE
) -> Settings:
    """Build settings; process environment variables win over the Hermes ``.env`` file."""
    source = os.environ if environ is None else environ
    env = read_env_file(env_file)
    env.update({key: value for key, value in source.items() if key.startswith(ENV_PREFIX)})

    base_url = env.get("DOEEDD_BASE_URL", "").strip().rstrip("/")
    token = env.get("DOEEDD_TOKEN", "").strip()
    required = (("DOEEDD_BASE_URL", base_url), ("DOEEDD_TOKEN", token))
    missing = [name for name, value in required if not value]
    if missing:
        raise ConfigError(f"Missing {', '.join(missing)}; set it in the environment or {env_file}")
    if not (base_url.startswith("https://") or base_url.startswith(LOCAL_URL_PREFIXES)):
        raise ConfigError("DOEEDD_BASE_URL must use https (plain http only for localhost)")

    timezone_name = env.get("DOEEDD_TIMEZONE", DEFAULT_TIMEZONE)
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Unknown DOEEDD_TIMEZONE {timezone_name!r}") from exc

    state_dir = Path(env.get("DOEEDD_STATE_DIR") or DEFAULT_STATE_DIR).expanduser()
    return Settings(base_url=base_url, token=token, timezone=timezone, state_dir=state_dir)
