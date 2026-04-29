from __future__ import annotations

import io
import os

import pytest

from saddler.agent.model import AgentSpec
from saddler.infra.harness.claude_code import ClaudeCodeHarness
from saddler.infra.harness.codex import CodexHarness
from saddler.infra.harness.cursor import CursorHarness
from saddler.infra.harness.gemini import GeminiHarness
from saddler.infra.harness.openclaw import OpenClawHarness
from saddler.infra.harness.opencode import OpenCodeHarness
from saddler.runtime.backend import Command, ProcessHandle


class _FakeHandle:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        os.close(out_w)
        os.close(err_w)
        self.stdout = os.fdopen(out_r, "rb", buffering=0)
        self.stderr = os.fdopen(err_r, "rb", buffering=0)
        self.returncode: int | None = 0

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def resize(self, rows: int, cols: int) -> None:
        _ = (rows, cols)

    def __enter__(self) -> _FakeHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        _ = (exc_type, exc, tb)
        self.stdout.close()
        self.stderr.close()


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def exec(  # noqa: ANN201
        self,
        command: Command,
        cwd: str,
        env=None,  # noqa: ANN001
        *,
        stdin: bool = False,
        stdout: bool = False,
        stderr: bool = False,
        tty: bool = False,
        detach: bool = False,
        timeout: float | None = None,
    ) -> ProcessHandle | None:
        self.calls.append(
            {
                "command": command,
                "stdin": stdin,
                "stdout": stdout,
                "tty": tty,
                "detach": detach,
            }
        )
        _ = (cwd, env, stdin, stdout, stderr, tty, timeout)
        if detach:
            return None
        if command == [  # openclaw gateway status checks in _ensure_gateway
            "openclaw",
            "gateway",
            "status",
            "--require-rpc",
        ]:
            return _FakeHandle()
        if command == "which claude-agent-acp":
            return _FakeHandle()
        if command == "which codex-acp":
            return _FakeHandle()
        return _FakeHandle()


@pytest.mark.parametrize(
    ("harness_cls", "harness_name", "acp_command_hint"),
    [
        (ClaudeCodeHarness, "claude-code", "claude-agent-acp"),
        (OpenCodeHarness, "opencode", "acp"),
        (GeminiHarness, "gemini", "--acp"),
        (CodexHarness, "codex", "codex-acp"),
        (CursorHarness, "cursor", "acp"),
        (OpenClawHarness, "openclaw", "acp"),
    ],
)
def test_harness_tui_and_acp_return_process_handle_with_stdio(
    harness_cls: type,
    harness_name: str,
    acp_command_hint: str,
) -> None:
    spec = AgentSpec(
        harness=harness_name,
        workdir="/workspace",
        role=None,
        skills=[],
        rules=[],
        harness_spec=None,
    )
    harness = harness_cls.from_spec(spec)
    runtime = _FakeRuntime()

    requested_tty = True
    tui_handle = harness.tui(runtime, tty=requested_tty)
    acp_handle = harness.acp(runtime, tty=requested_tty)

    for handle in (tui_handle, acp_handle):
        assert handle.stdin is not None
        assert handle.stdout is not None
        assert hasattr(handle, "wait")
        assert hasattr(handle, "poll")

    interactive_calls = [
        call
        for call in runtime.calls
        if call["stdin"] is True and call["stdout"] is True
    ]
    assert len(interactive_calls) == 2

    tui_call, acp_call = interactive_calls
    assert tui_call["tty"] is requested_tty
    assert acp_call["tty"] is requested_tty

    def _command_text(command: object) -> str:
        if isinstance(command, str):
            return command
        if isinstance(command, list):
            return " ".join(str(part) for part in command)
        return " ".join(command)

    tui_cmd = _command_text(tui_call["command"])
    acp_cmd = _command_text(acp_call["command"])
    assert tui_cmd != acp_cmd
    assert acp_command_hint in acp_cmd
