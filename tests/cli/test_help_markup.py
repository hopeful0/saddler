from typer.testing import CliRunner

import saddler.cli as cli
from saddler.cli import app


def test_agent_create_help_keeps_square_brackets() -> None:
    result = CliRunner().invoke(app, ["agent", "create", "--help"])

    assert result.exit_code == 0
    assert "[name@]source (repeatable)" in result.stdout


def test_global_version_flag_prints_and_exits(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_saddler_version", lambda: "9.9.9")
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "9.9.9"
