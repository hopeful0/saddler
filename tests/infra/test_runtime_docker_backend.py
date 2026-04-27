import io
import subprocess
import tarfile
from types import SimpleNamespace

import pytest
from docker.errors import NotFound

from saddler.infra.runtime.docker import (
    DockerPopen,
    DockerSubprocess,
    DockerRuntimeBackend,
    DockerRuntimeSpec,
    DockerRuntimeState,
)
from saddler.runtime.backend import Command
from saddler.runtime.model import RuntimeSpec


def test_docker_sdk_import_available() -> None:
    import docker

    assert hasattr(docker, "from_env")


def _make_backend() -> DockerRuntimeBackend:
    return DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker"),
        docker_spec=DockerRuntimeSpec(),
        state=DockerRuntimeState(container_id="cid-123"),
    )


class FakeContainer:
    def __init__(
        self,
        *,
        container_id: str = "new-cid",
        name: str = "new-name",
        running: bool = True,
    ) -> None:
        self.id = container_id
        self.name = name
        self.attrs = {"State": {"Running": running}}
        self.start_calls = 0
        self.reload_calls = 0
        self.stop_calls: list[int] = []
        self.remove_calls: list[bool] = []
        self.put_archive_calls: list[tuple[str, bytes]] = []
        self.exec_run_calls: list[tuple[list[str], str | None]] = []
        self.put_archive_result = True
        self.exec_run_result: tuple[int, bytes] = (0, b"")

    def start(self) -> None:
        self.start_calls += 1

    def reload(self) -> None:
        self.reload_calls += 1

    def stop(self, *, timeout: int) -> None:
        self.stop_calls.append(timeout)

    def remove(self, *, force: bool) -> None:
        self.remove_calls.append(force)

    def put_archive(self, path: str, data: bytes) -> bool:
        self.put_archive_calls.append((path, data))
        return self.put_archive_result

    def exec_run(self, cmd: list[str], user: str | None = None) -> tuple[int, bytes]:
        self.exec_run_calls.append((cmd, user))
        return self.exec_run_result


class FakeContainersApi:
    def __init__(self, *, run_container: FakeContainer | None = None) -> None:
        self.run_calls: list[dict[str, object]] = []
        self.get_calls: list[str] = []
        self._run_container = run_container or FakeContainer()
        self._get_result: FakeContainer | Exception = FakeContainer(
            container_id="cid-123", name="existing"
        )

    def set_get_result(self, result: FakeContainer | Exception) -> None:
        self._get_result = result

    def run(self, image: str, command: list[str], **kwargs: object) -> FakeContainer:
        self.run_calls.append({"image": image, "command": command, **kwargs})
        return self._run_container

    def get(self, container_id: str) -> FakeContainer:
        self.get_calls.append(container_id)
        if isinstance(self._get_result, Exception):
            raise self._get_result
        return self._get_result


def test_start_uses_client_containers_run_when_no_container_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker", env={"A": "1"}),
        docker_spec=DockerRuntimeSpec(image="python:3.12-slim"),
        state=DockerRuntimeState(container_id=None),
    )
    fake_containers = FakeContainersApi(
        run_container=FakeContainer(container_id="new-cid")
    )
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    backend.start()

    assert len(fake_containers.run_calls) == 1
    call = fake_containers.run_calls[0]
    assert call["image"] == "python:3.12-slim"
    assert call["detach"] is True
    assert call["environment"] == {"A": "1"}
    assert backend.state.container_id == "new-cid"


def test_start_uses_client_container_start_when_has_container_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    existing = FakeContainer(container_id="cid-123")
    fake_containers = FakeContainersApi()
    fake_containers.set_get_result(existing)
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    backend.start()

    assert fake_containers.get_calls == ["cid-123"]
    assert existing.start_calls == 1


def test_is_running_uses_container_attrs(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _make_backend()
    running_container = FakeContainer(container_id="cid-123", running=True)
    fake_containers = FakeContainersApi()
    fake_containers.set_get_result(running_container)
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    assert backend.is_running() is True
    assert running_container.reload_calls == 1


def test_is_running_returns_false_when_container_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    fake_containers = FakeContainersApi()
    fake_containers.set_get_result(NotFound("missing"))
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    assert backend.is_running() is False


def test_stop_uses_container_stop_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _make_backend()
    existing = FakeContainer(container_id="cid-123")
    fake_containers = FakeContainersApi()
    fake_containers.set_get_result(existing)
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    backend.stop()

    assert existing.stop_calls == [10]


def test_remove_uses_container_remove_and_clears_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    existing = FakeContainer(container_id="cid-123")
    fake_containers = FakeContainersApi()
    fake_containers.set_get_result(existing)
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    backend.remove()

    assert existing.remove_calls == [True]
    assert backend.state.container_id is None


class FakeDockerPopen:
    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        pid: int = 123,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.pid = pid
        self.attach_calls = 0

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        _ = timeout
        return self.stdout, self.stderr

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return self.exit_code

    def attach_to_stdio(self) -> None:
        self.attach_calls += 1


def _mux_frame(stream_type: int, payload: bytes) -> bytes:
    return bytes([stream_type, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


class FakeSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self._buf = b"".join(chunks)
        self.sent: list[bytes] = []
        self.shutdown_called = False

    def recv(self, size: int) -> bytes:
        if not self._buf:
            return b""
        chunk = self._buf[:size]
        self._buf = self._buf[size:]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def shutdown(self, _how: int) -> None:
        self.shutdown_called = True


def test_exec_exec_bg_exec_fg_all_use_spawn_docker_popen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    popen_calls: list[dict[str, object]] = []
    run_calls: list[dict[str, object]] = []

    class FakeSubprocess:
        def run(
            self, command: Command, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            run_calls.append({"command": command, **kwargs})
            return subprocess.CompletedProcess(command, 0, "ok", "")

        def Popen(self, command: Command, **kwargs: object) -> FakeDockerPopen:
            popen_calls.append({"command": command, **kwargs})
            return FakeDockerPopen(stdout="ok")

    monkeypatch.setattr(backend, "_docker_subprocess", lambda: FakeSubprocess())

    backend.exec("echo ok", cwd="/w")
    backend.exec_bg("echo ok", cwd="/w")
    backend.exec_fg("echo ok", cwd="/w")

    assert len(run_calls) == 1
    assert run_calls[0]["command"] == "echo ok"
    assert run_calls[0]["cwd"] == "/w"
    assert run_calls[0]["check"] is False
    assert len(popen_calls) == 2
    assert [call["mode"] for call in popen_calls] == ["bg", "fg"]


class FakeExecApi:
    def __init__(
        self,
        stream_chunks: list[bytes],
        inspect_sequence: list[int | None] | None = None,
    ) -> None:
        self.stream_chunks = stream_chunks
        self.exec_create_calls: list[dict[str, object]] = []
        self.last_socket: FakeSocket | None = None
        self.inspect_sequence = inspect_sequence or [0]
        self._inspect_idx = 0

    def exec_create(self, **kwargs: object) -> dict[str, str]:
        self.exec_create_calls.append(kwargs)
        return {"Id": "exec-1"}

    def exec_start(self, exec_id: str, **kwargs: object) -> list[bytes]:
        _ = exec_id
        if kwargs.get("socket"):
            sock = FakeSocket(self.stream_chunks)
            self.last_socket = sock
            return sock  # type: ignore[return-value]
        return self.stream_chunks

    def exec_inspect(self, exec_id: str) -> dict[str, int]:
        _ = exec_id
        idx = min(self._inspect_idx, len(self.inspect_sequence) - 1)
        self._inspect_idx += 1
        code = self.inspect_sequence[idx]
        return {"ExitCode": code}


def test_spawn_wraps_command_with_echo_pid_and_nested_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    fake_api = FakeExecApi([_mux_frame(1, b"123\n")])
    fake_client = SimpleNamespace(api=fake_api)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    popen = backend._spawn_docker_popen(
        command="echo hi", cwd="/workspace", env=None, mode="capture"
    )
    assert popen.pid == 123

    assert len(fake_api.exec_create_calls) == 1
    cmd = fake_api.exec_create_calls[0]["cmd"]
    assert cmd == ["sh", "-lc", "echo $$; exec sh -lc 'echo hi'"]


def test_spawn_raises_when_pid_handshake_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    fake_api = FakeExecApi([_mux_frame(1, b"not-a-pid\n")])
    fake_client = SimpleNamespace(api=fake_api)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    with pytest.raises(RuntimeError, match="pid handshake failed"):
        backend._spawn_docker_popen(
            command="echo hi", cwd="/workspace", env=None, mode="capture"
        )


def test_communicate_separates_stdout_and_stderr() -> None:
    api = FakeExecApi(
        [
            _mux_frame(1, b"123\n"),
            _mux_frame(2, b"err"),
            _mux_frame(1, b"out"),
        ],
        inspect_sequence=[None, 0],
    )
    proc = DockerPopen.spawn(
        api=api,
        container_id="cid-123",
        command="echo hi",
        cwd="/workspace",
        env=None,
        mode="capture",
    )

    stdout, stderr = proc.communicate(timeout=1)
    assert stdout == "out"
    assert stderr == "err"


def test_communicate_timeout_raises() -> None:
    api = FakeExecApi([_mux_frame(1, b"123\n")], inspect_sequence=[None, None, None])
    proc = DockerPopen.spawn(
        api=api,
        container_id="cid-123",
        command="sleep 10",
        cwd="/workspace",
        env=None,
        mode="capture",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        proc.communicate(timeout=0.01)


def test_communicate_accepts_input_and_closes_stdin() -> None:
    api = FakeExecApi(
        [
            _mux_frame(1, b"123\n"),
            _mux_frame(1, b"out"),
        ],
        inspect_sequence=[None, 0],
    )
    proc = DockerPopen.spawn(
        api=api,
        container_id="cid-123",
        command="cat",
        cwd="/workspace",
        env=None,
        mode="capture",
    )

    stdout, stderr = proc.communicate(input="hello", timeout=1)
    assert stdout == "out"
    assert stderr == ""
    assert api.last_socket is not None
    assert api.last_socket.sent == [b"hello"]
    assert api.last_socket.shutdown_called is True


def test_signal_methods_issue_kill_exec_calls() -> None:
    api = FakeExecApi([_mux_frame(1, b"123\n")], inspect_sequence=[0])
    proc = DockerPopen.spawn(
        api=api,
        container_id="cid-123",
        command="echo hi",
        cwd="/workspace",
        env=None,
        mode="capture",
    )

    proc.terminate()
    proc.kill()
    kill_cmds = [
        call.get("cmd")
        for call in api.exec_create_calls
        if isinstance(call.get("cmd"), list) and call.get("cmd", [None])[0] == "kill"
    ]
    assert ["kill", "-TERM", "123"] in kill_cmds
    assert ["kill", "-KILL", "123"] in kill_cmds


def test_docker_subprocess_run_check_raises_calledprocesserror() -> None:
    api = FakeExecApi(
        [
            _mux_frame(1, b"123\n"),
            _mux_frame(2, b"boom"),
        ],
        inspect_sequence=[None, 7],
    )
    subproc = DockerSubprocess(api=api, container_id="cid-123")

    with pytest.raises(subprocess.CalledProcessError) as exc:
        subproc.run("exit 7", cwd="/workspace", check=True)
    assert exc.value.returncode == 7


def test_docker_subprocess_run_timeout_cleans_up_process() -> None:
    api = FakeExecApi(
        [_mux_frame(1, b"123\n")],
        inspect_sequence=[None, None, None, None],
    )
    subproc = DockerSubprocess(api=api, container_id="cid-123")

    with pytest.raises(subprocess.TimeoutExpired):
        subproc.run("sleep 10", cwd="/workspace", timeout=0.01)

    kill_cmds = [
        call.get("cmd")
        for call in api.exec_create_calls
        if isinstance(call.get("cmd"), list) and call.get("cmd", [None])[0] == "kill"
    ]
    assert ["kill", "-TERM", "123"] in kill_cmds
    assert ["kill", "-KILL", "123"] in kill_cmds


def test_exec_non_zero_returns_result_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()

    class FakeSubprocess:
        def run(
            self, command: Command, **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 7, "", "boom")

        def Popen(self, command: Command, **kwargs: object) -> FakeDockerPopen:
            return FakeDockerPopen()

    monkeypatch.setattr(backend, "_docker_subprocess", lambda: FakeSubprocess())

    result = backend.exec("exit 7", cwd="/workspace")

    assert result.exit_code == 7
    assert result.stderr == "boom"


def test_exec_fg_raises_runtime_error_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    fake_proc = FakeDockerPopen(exit_code=7)
    monkeypatch.setattr(backend, "_spawn_docker_popen", lambda **_: fake_proc)
    with pytest.raises(RuntimeError, match="exit code 7"):
        backend.exec_fg("exit 7", cwd="/workspace")
    assert fake_proc.attach_calls == 1


class FakeArchiveApi:
    def __init__(self, archive_bytes: bytes, stat: dict[str, object]) -> None:
        self.archive_bytes = archive_bytes
        self.stat = stat
        self.get_archive_calls: list[tuple[str, str]] = []

    def get_archive(
        self, container_id: str, src_runtime: str
    ) -> tuple[list[bytes], dict[str, object]]:
        self.get_archive_calls.append((container_id, src_runtime))
        return [self.archive_bytes], self.stat


def _make_single_file_archive(name: str, content: str) -> bytes:
    buf = io.BytesIO()
    encoded = content.encode()
    info = tarfile.TarInfo(name=name)
    info.size = len(encoded)
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.addfile(info, io.BytesIO(encoded))
    return buf.getvalue()


def test_copy_to_uses_put_archive_and_chown_when_user_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    backend = DockerRuntimeBackend(
        spec=RuntimeSpec(backend_type="docker"),
        docker_spec=DockerRuntimeSpec(user="1000:1000"),
        state=DockerRuntimeState(container_id="cid-123"),
    )
    fake_container = FakeContainer(container_id="cid-123")
    fake_containers = FakeContainersApi()
    fake_containers.set_get_result(fake_container)
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    src = tmp_path / "a.txt"
    src.write_text("x")

    backend.copy_to(str(src), "/work/a.txt")

    assert len(fake_container.put_archive_calls) == 1
    put_path, payload = fake_container.put_archive_calls[0]
    assert put_path == "/work"
    assert payload
    assert fake_container.exec_run_calls == [
        (["chown", "-R", "1000:1000", "/work/a.txt"], "0")
    ]


def test_copy_to_directory_uses_parent_put_path_and_dest_basename(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    backend = _make_backend()
    fake_container = FakeContainer(container_id="cid-123")
    fake_containers = FakeContainersApi()
    fake_containers.set_get_result(fake_container)
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    skill_dir = tmp_path / "docx"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("x")

    backend.copy_to(str(skill_dir), "/home/pn/.opencode/skills/docx")

    assert len(fake_container.put_archive_calls) == 1
    put_path, payload = fake_container.put_archive_calls[0]
    assert put_path == "/home/pn/.opencode/skills"
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as tar:
        names = sorted(tar.getnames())
    assert names == ["docx", "docx/SKILL.md"]


def test_copy_from_uses_get_archive(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    backend = _make_backend()
    archive = _make_single_file_archive("out.txt", "hello")
    fake_api = FakeArchiveApi(
        archive_bytes=archive, stat={"name": "out.txt", "mode": 0o100644}
    )
    fake_client = SimpleNamespace(api=fake_api)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    dest = tmp_path / "out.txt"
    backend.copy_from("/work/out.txt", str(dest))

    assert fake_api.get_archive_calls == [("cid-123", "/work/out.txt")]
    assert dest.read_text() == "hello"
