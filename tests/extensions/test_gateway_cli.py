from __future__ import annotations

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
