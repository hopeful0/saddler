import shlex
import subprocess

import pytest

from saddler.infra.runtime.docker import (
    DockerRuntimeBackend,
    DockerRuntimeSpec,
    DockerRuntimeState,
)
from saddler.runtime.model import RuntimeSpec


def _make_backend() -> DockerRuntimeBackend:
    return DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker"),
        docker_spec=DockerRuntimeSpec(),
        state=DockerRuntimeState(container_id="cid-123"),
    )


def test_exec_wraps_command_with_sh_lc(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _make_backend()
    captured: dict[str, object] = {}

    def fake_run_subprocess(
        cmd: list[str], *, timeout: float | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        captured["check"] = check
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(backend, "_run_subprocess", fake_run_subprocess)

    result = backend.exec("echo hello", cwd="/workspace", timeout=5)

    assert result.exit_code == 0
    assert result.stdout == "ok"
    assert captured["cmd"] == [
        "docker",
        "exec",
        "-w",
        "/workspace",
        "cid-123",
        "sh",
        "-lc",
        "echo hello",
    ]
    assert captured["timeout"] == 5
    assert captured["check"] is False


def test_exec_bg_wraps_list_command_with_sh_lc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    captured: dict[str, object] = {}

    def fake_run_docker(args: list[str]) -> str:
        captured["args"] = args
        return ""

    monkeypatch.setattr(backend, "_run_docker", fake_run_docker)

    backend.exec_bg(["echo", "hello world"], cwd="/work")

    assert captured["args"] == [
        "exec",
        "-d",
        "-w",
        "/work",
        "cid-123",
        "sh",
        "-lc",
        shlex.join(["echo", "hello world"]),
    ]


def test_exec_fg_raises_runtime_error_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()

    def fake_subprocess_run(
        cmd: list[str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(
        "saddler.infra.runtime.docker.subprocess.run",
        fake_subprocess_run,
    )

    with pytest.raises(RuntimeError, match="exit code 7"):
        backend.exec_fg("exit 7", cwd="/workspace")
