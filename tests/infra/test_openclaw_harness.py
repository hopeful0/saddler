from __future__ import annotations

import io
import os
from dataclasses import dataclass

import pytest

from saddler.agent.model import AgentSpec
from saddler.infra.harness.openclaw import OpenClawHarness
from saddler.runtime.backend import Command, ProcessHandle


class _FakeHandle:
    def __init__(self, exit_code: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode: int | None = exit_code
        self.stdin = io.BytesIO()
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        os.write(out_w, stdout)
        os.write(err_w, stderr)
        os.close(out_w)
        os.close(err_w)
        self.stdout = os.fdopen(out_r, "rb", buffering=0)
        self.stderr = os.fdopen(err_r, "rb", buffering=0)

    def wait(self, timeout: float | None = None) -> int:
        return int(self.returncode or 0)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def resize(self, rows: int, cols: int) -> None:
        pass

    def __enter__(self) -> _FakeHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stdout.close()
        self.stderr.close()


@dataclass
class _FakeRuntime:
    status_failures_before_ready: int

    def __post_init__(self) -> None:
        self.status_calls = 0
        self.started_gateway = False

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
        _ = (cwd, env, stdin, stdout, stderr, tty, timeout)
        if detach:
            if command == ["openclaw", "gateway", "--allow-unconfigured"]:
                self.started_gateway = True
            return None
        if command == ["openclaw", "gateway", "status", "--require-rpc"]:
            self.status_calls += 1
            if self.status_calls <= self.status_failures_before_ready:
                return _FakeHandle(exit_code=1, stdout=b"", stderr=b"not ready")
            return _FakeHandle(exit_code=0, stdout=b"ok", stderr=b"")
        return _FakeHandle(exit_code=0, stdout=b"", stderr=b"")


def _build_harness(timeout_seconds: float = 0.2) -> OpenClawHarness:
    spec = AgentSpec(
        harness="openclaw",
        workdir="/workspace",
        role=None,
        skills=[],
        rules=[],
        harness_spec={
            "gateway_wait_timeout_seconds": timeout_seconds,
            "gateway_poll_interval_seconds": 0.01,
        },
    )
    return OpenClawHarness.from_spec(spec)


def test_openclaw_gateway_waits_until_ready() -> None:
    harness = _build_harness(timeout_seconds=0.3)
    runtime = _FakeRuntime(status_failures_before_ready=2)

    harness._ensure_gateway(runtime)  # noqa: SLF001

    assert runtime.started_gateway is True
    assert runtime.status_calls >= 3


def test_openclaw_gateway_raises_on_timeout() -> None:
    harness = _build_harness(timeout_seconds=0.05)
    runtime = _FakeRuntime(status_failures_before_ready=9999)

    with pytest.raises(RuntimeError, match="did not become ready before timeout"):
        harness._ensure_gateway(runtime)  # noqa: SLF001
