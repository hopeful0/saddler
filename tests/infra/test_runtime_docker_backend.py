import shlex
import subprocess
from dataclasses import dataclass

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


@dataclass
class _FakeContainer:
    id: str = "cid-123"
    status: str = "running"
    start_calls: int = 0
    stop_timeout: int | None = None
    remove_force: bool | None = None
    reload_calls: int = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self, *, timeout: int) -> None:
        self.stop_timeout = timeout

    def remove(self, *, force: bool) -> None:
        self.remove_force = force

    def reload(self) -> None:
        self.reload_calls += 1


class _FakeContainerCollection:
    def __init__(
        self,
        *,
        run_container: _FakeContainer | None = None,
        get_container: _FakeContainer | None = None,
    ) -> None:
        self.run_container = run_container or _FakeContainer(id="cid-new")
        self.get_container = get_container or _FakeContainer()
        self.run_kwargs: dict[str, object] | None = None
        self.get_calls: list[str] = []

    def run(self, **kwargs: object) -> _FakeContainer:
        self.run_kwargs = kwargs
        return self.run_container

    def get(self, container_id: str) -> _FakeContainer:
        self.get_calls.append(container_id)
        return self.get_container


class _FakeDockerClient:
    def __init__(self, containers: _FakeContainerCollection) -> None:
        self.containers = containers
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_start_runs_container_and_persists_state_via_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    containers = _FakeContainerCollection(run_container=_FakeContainer(id="cid-run"))
    client = _FakeDockerClient(containers)
    backend = DockerRuntimeBackend(
        spec=RuntimeSpec(
            backend_type="docker",
            env={"ENV_A": "value-a"},
            mounts=[
                {
                    "type": "bind",
                    "source": "/host-data",
                    "destination": "/workspace/data",
                    "mode": "ro",
                }
            ],
        ),
        docker_spec=DockerRuntimeSpec(
            image="python:3.12-slim",
            container_name="runtime-demo",
            command=["sleep", "infinity"],
            user="1000:1000",
        ),
        state=DockerRuntimeState(),
    )
    monkeypatch.setattr(
        DockerRuntimeBackend,
        "_docker_client",
        staticmethod(lambda: client),
    )

    backend.start()

    assert containers.run_kwargs == {
        "image": "python:3.12-slim",
        "command": ["sleep", "infinity"],
        "detach": True,
        "name": "runtime-demo",
        "user": "1000:1000",
        "environment": {"ENV_A": "value-a"},
        "volumes": {
            "/host-data": {"bind": "/workspace/data", "mode": "ro"},
        },
    }
    assert backend.state.container_id == "cid-run"
    assert backend.state.container_name == "runtime-demo"
    assert backend.state.image == "python:3.12-slim"
    assert client.closed is True


def test_start_existing_container_calls_get_start_via_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _FakeContainer(id="cid-existing")
    containers = _FakeContainerCollection(get_container=existing)
    client = _FakeDockerClient(containers)
    backend = DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker"),
        docker_spec=DockerRuntimeSpec(),
        state=DockerRuntimeState(container_id="cid-existing"),
    )
    monkeypatch.setattr(
        DockerRuntimeBackend,
        "_docker_client",
        staticmethod(lambda: client),
    )

    backend.start()

    assert containers.get_calls == ["cid-existing"]
    assert existing.start_calls == 1
    assert containers.run_kwargs is None
    assert client.closed is True


def test_stop_remove_and_is_running_go_through_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked = _FakeContainer(id="cid-123", status="running")
    containers = _FakeContainerCollection(get_container=tracked)
    client = _FakeDockerClient(containers)
    backend = DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker"),
        docker_spec=DockerRuntimeSpec(),
        state=DockerRuntimeState(
            container_id="cid-123",
            container_name="rt-name",
            image="python:3.12-slim",
        ),
    )
    monkeypatch.setattr(
        DockerRuntimeBackend,
        "_docker_client",
        staticmethod(lambda: client),
    )

    assert backend.is_running() is True
    backend.stop()
    backend.remove()

    assert containers.get_calls == ["cid-123", "cid-123", "cid-123"]
    assert tracked.reload_calls == 1
    assert tracked.stop_timeout == 10
    assert tracked.remove_force is True
    assert backend.state.container_id is None
    assert backend.state.container_name == "rt-name"
    assert backend.state.image == "python:3.12-slim"
    assert client.closed is True


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
