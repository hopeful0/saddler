from __future__ import annotations

from dataclasses import dataclass

import pytest

from saddler.agent.model import AgentSpec
from saddler.infra.harness.openclaw import OpenClawHarness
from saddler.runtime.backend import ExecResult


@dataclass
class _FakeRuntime:
    status_failures_before_ready: int

    def __post_init__(self) -> None:
        self.status_calls = 0
        self.started_gateway = False

    def exec(self, command: list[str], cwd: str, env=None, timeout=None) -> ExecResult:  # noqa: ANN001
        if command == ["openclaw", "gateway", "status", "--require-rpc"]:
            self.status_calls += 1
            if self.status_calls <= self.status_failures_before_ready:
                return ExecResult(exit_code=1, stdout="", stderr="not ready")
            return ExecResult(exit_code=0, stdout="ok", stderr="")
        return ExecResult(exit_code=0, stdout="", stderr="")

    def exec_bg(self, command: list[str], cwd: str, env=None) -> None:  # noqa: ANN001
        if command == ["openclaw", "gateway", "--allow-unconfigured"]:
            self.started_gateway = True


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
