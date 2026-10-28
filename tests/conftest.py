import os
from pathlib import Path

import pytest

import finance_agent.config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests away from the developer's real ``.env`` files and ``FINANCE_*`` variables."""
    monkeypatch.setattr(finance_agent.config, "USER_ENV", tmp_path / "no-user.env")
    monkeypatch.setattr(finance_agent.config, "PROJECT_ENV", tmp_path / "no-project.env")
    for name in [key for key in os.environ if key.startswith("FINANCE_")]:
        monkeypatch.delenv(name)
