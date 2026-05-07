from __future__ import annotations

from typer.testing import CliRunner

from saddler.extensions.gateway import cli


def test_resolve_token_prefers_cli_over_env(monkeypatch) -> None:
    monkeypatch.setenv("SADDLER_GATEWAY_TOKEN", "env-token")
    assert cli._resolve_token("cli-token") == "cli-token"


def test_resolve_token_uses_env_when_cli_missing(monkeypatch) -> None:
    monkeypatch.setenv("SADDLER_GATEWAY_TOKEN", "env-token")
    assert cli._resolve_token(None) == "env-token"


def test_resolve_token_generates_random_when_cli_and_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("SADDLER_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda _: "random-token")
    assert cli._resolve_token(None) == "random-token"


def test_connect_tui_exits_when_stdin_not_tty(monkeypatch) -> None:
    monkeypatch.setenv("SADDLER_GATEWAY_TOKEN", "tok")
    monkeypatch.setattr(
        "saddler.extensions.gateway.cli.sys.stdin.isatty", lambda: False
    )
    runner = CliRunner()
    result = runner.invoke(
        cli.gateway_app,
        ["connect", "http://127.0.0.1:9", "a1", "--tui"],
    )
    assert result.exit_code == 1
