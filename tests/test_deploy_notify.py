"""The Railway cron wrapper ``deploy/hermes/doeedd_notify.py``."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "hermes" / "doeedd_notify.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("doeedd_notify", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_command_uses_the_image_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    notify = load_script()
    monkeypatch.setenv("FINANCE_AGENT_DIR", "/opt/finance-agent")
    monkeypatch.setenv("HERMES_PYTHON", "/opt/hermes/.venv/bin/python")
    assert notify.cli_command() == [
        "/opt/hermes/.venv/bin/python",
        "/opt/finance-agent/doeedd.py",
        "notify",
        "--empty-when-silent",
    ]


def test_command_falls_back_to_the_vm_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    notify = load_script()
    monkeypatch.delenv("FINANCE_AGENT_DIR", raising=False)
    monkeypatch.delenv("HERMES_PYTHON", raising=False)
    command = notify.cli_command()
    assert command[0] == sys.executable
    assert command[1] == str(Path.home() / "finance-system" / "doeedd.py")


def fake_run(
    returncode: int, stdout: str, stderr: str
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    return run


def test_message_is_passed_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notify = load_script()
    monkeypatch.setattr(notify.subprocess, "run", fake_run(0, "💸 Food at 80%\n", ""))
    assert notify.main() == 0
    assert capsys.readouterr().out == "💸 Food at 80%\n"


def test_silence_prints_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notify = load_script()
    monkeypatch.setattr(notify.subprocess, "run", fake_run(0, "", ""))
    assert notify.main() == 0
    assert capsys.readouterr().out == ""


def test_failure_keeps_stdout_empty_and_returns_the_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notify = load_script()
    monkeypatch.setattr(notify.subprocess, "run", fake_run(2, "partial", "x" * 600 + "boom"))
    assert notify.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip().endswith("boom")
    assert len(captured.err.strip()) == 500
