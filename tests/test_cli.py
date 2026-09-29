import json
from pathlib import Path

from finance_agent.cli import main


def test_json_cli_works_from_any_directory(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FINANCE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    assert main(["--json", "record", "--amount", "25000", "--key", "message:1"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "created"
    assert main(["--json", "add", "--amount", "25000", "--key", "message:1"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "replayed"
    assert main(["--json", "summary"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 1
