import io
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from saddler.infra.runtime.docker import (
    _DEFAULT_PID_HANDSHAKE_TIMEOUT_SECONDS,
    DockerRuntimeBackend,
    DockerPopen,
    DockerRuntimeSpec,
    DockerRuntimeState,
)
from saddler.runtime.backend import ExecResult
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


@dataclass
class _FakeExecRunResult:
    exit_code: int = 0
    output: bytes = b""


@dataclass
class _FakeArchiveContainer:
    put_archive_result: bool = True
    get_archive_stream: list[bytes] = field(default_factory=list)
    get_archive_stat: dict[str, object] = field(default_factory=dict)
    exec_run_result: _FakeExecRunResult = field(default_factory=_FakeExecRunResult)
    put_archive_calls: list[dict[str, object]] = field(default_factory=list)
    get_archive_calls: list[str] = field(default_factory=list)
    exec_run_calls: list[dict[str, object]] = field(default_factory=list)

    def put_archive(self, path: str, data: bytes) -> bool:
        self.put_archive_calls.append({"path": path, "data": data})
        return self.put_archive_result

    def get_archive(self, path: str) -> tuple[list[bytes], dict[str, object]]:
        self.get_archive_calls.append(path)
        return self.get_archive_stream, self.get_archive_stat

    def exec_run(
        self,
        cmd: list[str],
        *,
        user: str | None = None,
        workdir: str | None = None,
    ) -> _FakeExecRunResult:
        self.exec_run_calls.append({"cmd": cmd, "user": user, "workdir": workdir})
        return self.exec_run_result


class _FakeContainerCollection:
    def __init__(
        self,
        *,
        run_container: object | None = None,
        get_container: object | None = None,
    ) -> None:
        self.run_container = run_container or _FakeContainer(id="cid-new")
        self.get_container = get_container or _FakeContainer()
        self.run_kwargs: dict[str, object] | None = None
        self.get_calls: list[str] = []

    def run(self, **kwargs: object) -> object:
        self.run_kwargs = kwargs
        return self.run_container

    def get(self, container_id: str) -> object:
        self.get_calls.append(container_id)
        return self.get_container


class _FakeDockerClient:
    def __init__(self, containers: _FakeContainerCollection) -> None:
        self.containers = containers
        self.closed = False
        self.api = object()

    def close(self) -> None:
        self.closed = True


class _FakeSpawnClient:
    def __init__(self, api: object | None = None) -> None:
        self.api = api if api is not None else object()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakePopen:
    def __init__(
        self,
        *,
        exec_result: ExecResult | None = None,
        fg_exit_code: int = 0,
    ) -> None:
        self.exec_result = exec_result or ExecResult(
            exit_code=0,
            stdout="",
            stderr="",
        )
        self.fg_exit_code = fg_exit_code
        self.wait_capture_calls = 0
        self.detach_calls = 0
        self.wait_foreground_calls = 0

    def wait_capture(self) -> ExecResult:
        self.wait_capture_calls += 1
        return self.exec_result

    def detach(self) -> None:
        self.detach_calls += 1

    def wait_foreground(self) -> int:
        self.wait_foreground_calls += 1
        return self.fg_exit_code


def _build_tar_payload(
    *,
    files: dict[str, bytes],
    directories: list[str] | None = None,
) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        for directory in directories or []:
            info = tarfile.TarInfo(name=directory.rstrip("/") + "/")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return payload.getvalue()


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


def test_spawn_docker_popen_wraps_with_pid_handshake_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    fake_client = _FakeSpawnClient(api=object())
    captured: dict[str, object] = {"kwargs": None}
    fake_popen = object()

    monkeypatch.setattr(backend, "_docker_client", lambda: fake_client)

    def fake_spawn(**kwargs: object) -> object:
        captured["kwargs"] = kwargs
        return fake_popen

    monkeypatch.setattr(DockerPopen, "spawn", staticmethod(fake_spawn))

    popen, client = backend._spawn_docker_popen(
        command=["echo", "hello world"],
        cwd="/workspace",
        env={"A": "1"},
        timeout=5.0,
        tty=True,
    )

    assert popen is fake_popen
    assert client is fake_client
    assert captured["kwargs"] == {
        "api": fake_client.api,
        "container_id": "cid-123",
        "wrapped_command": "echo $$; exec sh -lc \"echo 'hello world'\"",
        "cwd": "/workspace",
        "env": {"A": "1"},
        "timeout": 5.0,
        "handshake_timeout": _DEFAULT_PID_HANDSHAKE_TIMEOUT_SECONDS,
        "tty": True,
    }


def test_exec_returns_exec_result_without_raising_on_non_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    fake_popen = _FakePopen(
        exec_result=ExecResult(exit_code=7, stdout="out", stderr="err")
    )
    fake_client = _FakeSpawnClient()

    monkeypatch.setattr(
        backend,
        "_spawn_docker_popen",
        lambda **_kwargs: (fake_popen, fake_client),
    )

    result = backend.exec("exit 7", cwd="/workspace")

    assert result == ExecResult(exit_code=7, stdout="out", stderr="err")
    assert fake_popen.wait_capture_calls == 1
    assert fake_client.closed is True


def test_exec_bg_delegates_to_spawn_and_detach(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _make_backend()
    fake_popen = _FakePopen()
    fake_client = _FakeSpawnClient()
    captured: dict[str, object] = {"kwargs": None}

    def fake_spawn(**kwargs: object) -> tuple[_FakePopen, _FakeSpawnClient]:
        captured["kwargs"] = kwargs
        return fake_popen, fake_client

    monkeypatch.setattr(backend, "_spawn_docker_popen", fake_spawn)

    backend.exec_bg("sleep 1", cwd="/workspace", env={"B": "2"})

    assert captured["kwargs"] == {
        "command": "sleep 1",
        "cwd": "/workspace",
        "env": {"B": "2"},
        "timeout": None,
        "tty": False,
    }
    assert fake_popen.detach_calls == 1
    assert fake_client.closed is True


def test_exec_fg_raises_runtime_error_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    fake_popen = _FakePopen(fg_exit_code=9)
    fake_client = _FakeSpawnClient()

    monkeypatch.setattr(
        backend,
        "_spawn_docker_popen",
        lambda **_kwargs: (fake_popen, fake_client),
    )

    with pytest.raises(RuntimeError, match="exit code 9"):
        backend.exec_fg("exit 7", cwd="/workspace")

    assert fake_popen.wait_foreground_calls == 1
    assert fake_client.closed is True


def test_exec_fg_uses_interactive_tty_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _make_backend()
    fake_popen = _FakePopen(fg_exit_code=0)
    fake_client = _FakeSpawnClient()
    captured: dict[str, object] = {"kwargs": None}

    def fake_spawn(**kwargs: object) -> tuple[_FakePopen, _FakeSpawnClient]:
        captured["kwargs"] = kwargs
        return fake_popen, fake_client

    monkeypatch.setattr(backend, "_spawn_docker_popen", fake_spawn)
    monkeypatch.setattr("saddler.infra.runtime.docker.sys.stdin.isatty", lambda: True)

    backend.exec_fg("echo ok", cwd="/workspace")

    assert captured["kwargs"] == {
        "command": "echo ok",
        "cwd": "/workspace",
        "env": None,
        "timeout": None,
        "tty": True,
    }
    assert fake_popen.wait_foreground_calls == 1
    assert fake_client.closed is True


def test_copy_to_uses_sdk_put_archive_and_fixes_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    src = tmp_path / "hello.txt"
    src.write_text("hello runtime\n", encoding="utf-8")
    archive_container = _FakeArchiveContainer()
    containers = _FakeContainerCollection(get_container=archive_container)
    client = _FakeDockerClient(containers)
    backend = DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker"),
        docker_spec=DockerRuntimeSpec(user="1000:1000"),
        state=DockerRuntimeState(container_id="cid-123"),
    )
    monkeypatch.setattr(
        DockerRuntimeBackend,
        "_docker_client",
        staticmethod(lambda: client),
    )

    backend.copy_to(str(src), "/workspace/data/hello.txt")

    assert containers.get_calls == ["cid-123"]
    assert len(archive_container.put_archive_calls) == 1
    assert archive_container.put_archive_calls[0]["path"] == "/"
    raw_tar = archive_container.put_archive_calls[0]["data"]
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        member = archive.extractfile("workspace/data/hello.txt")
        assert member is not None
        assert member.read().decode("utf-8") == "hello runtime\n"
    assert archive_container.exec_run_calls == [
        {
            "cmd": ["chown", "-R", "1000:1000", "/workspace/data/hello.txt"],
            "user": "0",
            "workdir": "/",
        }
    ]
    assert client.closed is True


def test_copy_from_uses_sdk_get_archive_for_single_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_payload = _build_tar_payload(
        files={"workspace/out/result.txt": b"result from runtime\n"}
    )
    archive_container = _FakeArchiveContainer(
        get_archive_stream=[archive_payload[:10], archive_payload[10:]],
        get_archive_stat={"name": "workspace/out/result.txt"},
    )
    containers = _FakeContainerCollection(get_container=archive_container)
    client = _FakeDockerClient(containers)
    backend = DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker"),
        docker_spec=DockerRuntimeSpec(),
        state=DockerRuntimeState(container_id="cid-123"),
    )
    monkeypatch.setattr(
        DockerRuntimeBackend,
        "_docker_client",
        staticmethod(lambda: client),
    )
    dest = tmp_path / "downloaded.txt"

    backend.copy_from("/workspace/out/result.txt", str(dest))

    assert containers.get_calls == ["cid-123"]
    assert archive_container.get_archive_calls == ["/workspace/out/result.txt"]
    assert dest.read_text(encoding="utf-8") == "result from runtime\n"
    assert client.closed is True


def test_copy_from_directory_keeps_basename_when_dest_is_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_payload = _build_tar_payload(
        files={"workspace/pkg/file.txt": b"package payload"},
        directories=["workspace/pkg"],
    )
    archive_container = _FakeArchiveContainer(
        get_archive_stream=[archive_payload],
        get_archive_stat={"name": "workspace/pkg"},
    )
    containers = _FakeContainerCollection(get_container=archive_container)
    client = _FakeDockerClient(containers)
    backend = DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker"),
        docker_spec=DockerRuntimeSpec(),
        state=DockerRuntimeState(container_id="cid-123"),
    )
    monkeypatch.setattr(
        DockerRuntimeBackend,
        "_docker_client",
        staticmethod(lambda: client),
    )
    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir()

    backend.copy_from("/workspace/pkg", str(dest_dir))

    copied = dest_dir / "pkg" / "file.txt"
    assert copied.read_text(encoding="utf-8") == "package payload"
    assert client.closed is True
