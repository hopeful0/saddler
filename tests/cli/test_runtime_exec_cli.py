from __future__ import annotations

from saddler.runtime.backend import ExecResult
from typer.testing import CliRunner

import saddler.cli as cli
from saddler.cli import app


class _DummyRuntimeApi:
    def __init__(self, result: ExecResult) -> None:
        self._result = result
        self.calls: list[dict] = []
        self.fg_calls: list[dict] = []

    def exec(
        self,
        ref: str,
        command: str | list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        self.calls.append(
            {
                "ref": ref,
                "command": command,
                "cwd": cwd,
                "env": env,
                "timeout": timeout,
            }
        )
        return self._result

    def exec_fg(
        self,
        ref: str,
        command: str | list[str],
        *,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        self.fg_calls.append(
            {
                "ref": ref,
                "command": command,
                "cwd": cwd,
                "env": env,
            }
        )


def test_runtime_exec_passes_command_and_options(monkeypatch) -> None:
    runtime_api = _DummyRuntimeApi(ExecResult(exit_code=0, stdout="ok\n", stderr=""))
    monkeypatch.setattr(cli, "_runtime_api", lambda: runtime_api)

    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "exec",
            "rt-demo",
            "--cwd",
            "/workspace",
            "--env",
            "FOO=bar",
            "--timeout",
            "2.5",
            "--",
            "echo",
            "hello",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert runtime_api.calls == [
        {
            "ref": "rt-demo",
            "command": ["echo", "hello"],
            "cwd": "/workspace",
            "env": {"FOO": "bar"},
            "timeout": 2.5,
        }
    ]


def test_runtime_exec_returns_nonzero_exit_code(monkeypatch) -> None:
    runtime_api = _DummyRuntimeApi(
        ExecResult(exit_code=7, stdout="partial\n", stderr="boom\n")
    )
    monkeypatch.setattr(cli, "_runtime_api", lambda: runtime_api)

    result = CliRunner().invoke(app, ["runtime", "exec", "rt-demo", "--", "false"])

    assert result.exit_code == 7
    assert "partial\n" in result.stdout
    assert "boom\n" in result.stderr


def test_runtime_exec_help_shows_command_passthrough_usage() -> None:
    result = CliRunner().invoke(app, ["runtime", "exec", "--help"])

    assert result.exit_code == 0
    assert "runtime exec [OPTIONS] REF" in result.stdout
    assert "before command if needed." in result.stdout


def test_runtime_exec_interactive_uses_foreground_execution(monkeypatch) -> None:
    runtime_api = _DummyRuntimeApi(ExecResult(exit_code=0, stdout="", stderr=""))
    monkeypatch.setattr(cli, "_runtime_api", lambda: runtime_api)

    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "exec",
            "rt-demo",
            "--interactive",
            "--env",
            "FOO=bar",
            "--",
            "sh",
            "-lc",
            "echo hi",
        ],
    )

    assert result.exit_code == 0
    assert runtime_api.calls == []
    assert runtime_api.fg_calls == [
        {
            "ref": "rt-demo",
            "command": ["sh", "-lc", "echo hi"],
            "cwd": "/",
            "env": {"FOO": "bar"},
        }
    ]
