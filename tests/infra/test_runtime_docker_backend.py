import io
import os
import subprocess
import tarfile
from types import SimpleNamespace

import pytest
from docker.errors import NotFound

from saddler.infra.runtime.docker import (
    DockerSubprocess,
    DockerRuntimeBackend,
    DockerRuntimeSpec,
    DockerRuntimeState,
)
from saddler.runtime.backend import exec_bg, exec_capture, exec_fg
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
        self.exec_run_calls: list[tuple] = []
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

    def put_archive(self, path: str, data: bytes | io.IOBase) -> bool:
        raw = data if isinstance(data, bytes) else data.read()
        self.put_archive_calls.append((path, raw))
        return self.put_archive_result

    def exec_run(self, cmd: list[str], **kwargs: object) -> tuple[int, bytes]:
        self.exec_run_calls.append((cmd, kwargs))
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
    def __init__(self, *, exit_code: int = 0, pid: int = 123) -> None:
        self.exit_code = exit_code
        self.pid = pid
        self.returncode: int | None = None

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        self.returncode = self.exit_code
        return b"", b""

    def wait(self, timeout: float | None = None) -> int:
        return self.exit_code


def test_exec_capture_and_exec_fg_work_with_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())

    class FakeHandle:
        def __init__(self, out: bytes = b"ok", err: bytes = b"", code: int = 0) -> None:
            self.returncode: int | None = code
            self.stdin = io.BytesIO()
            out_r, out_w = os.pipe()
            err_r, err_w = os.pipe()
            os.write(out_w, out)
            os.write(err_w, err)
            os.close(out_w)
            os.close(err_w)
            self.stdout = os.fdopen(out_r, "rb", buffering=0)
            self.stderr = os.fdopen(err_r, "rb", buffering=0)

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            return int(self.returncode or 0)

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            return

        def kill(self) -> None:
            return

        def resize(self, rows: int, cols: int) -> None:
            _ = (rows, cols)

        def __enter__(self):
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            self.stdout.close()
            self.stderr.close()

    monkeypatch.setattr(backend, "exec", lambda *args, **kwargs: FakeHandle())
    result = exec_capture(backend, "echo ok", cwd="/w")
    assert result.exit_code == 0
    assert result.stdout == "ok"
    exec_fg(backend, "echo ok", cwd="/w")


def test_exec_bg_uses_exec_run_with_detach(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _make_backend()
    fake_container = FakeContainer(container_id="cid-123")
    fake_containers = FakeContainersApi()
    fake_containers.set_get_result(fake_container)
    fake_client = SimpleNamespace(containers=fake_containers)
    monkeypatch.setattr(backend, "_client", lambda: fake_client)

    exec_bg(backend, "sleep 10", cwd="/workspace")

    assert len(fake_container.exec_run_calls) == 1
    cmd, kwargs = fake_container.exec_run_calls[0]
    assert cmd == ["sh", "-lc", "sleep 10"]
    assert kwargs.get("detach") is True
    assert kwargs.get("workdir") == "/workspace"
    assert kwargs.get("environment") is None


def test_spawn_passes_user_to_exec_create() -> None:
    fake_api = FakeExecApi([_mux_frame(1, b"123\n")])
    DockerSubprocess(api=fake_api, container_id="cid-123", user="1000:1000").Popen(
        "echo hi",
        cwd="/workspace",
    )
    assert fake_api.exec_create_calls[0].get("user") == "1000:1000"


def test_spawn_passes_empty_user_when_unset() -> None:
    fake_api = FakeExecApi([_mux_frame(1, b"123\n")])
    DockerSubprocess(api=fake_api, container_id="cid-123").Popen(
        "echo hi",
        cwd="/workspace",
    )
    assert fake_api.exec_create_calls[0].get("user") == ""


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


def test_spawn_wraps_command_with_echo_pid_and_nested_shell() -> None:
    fake_api = FakeExecApi([_mux_frame(1, b"123\n")])
    popen = DockerSubprocess(api=fake_api, container_id="cid-123").Popen(
        "echo hi",
        cwd="/workspace",
        env=None,
    )
    assert popen.pid == 123
    assert len(fake_api.exec_create_calls) == 1
    cmd = fake_api.exec_create_calls[0]["cmd"]
    assert cmd == ["sh", "-lc", "echo $$; exec sh -lc 'echo hi'"]


def test_spawn_uses_tty_protocol_when_interactive_true() -> None:
    api = FakeExecApi([b"123\n"])
    proc = DockerSubprocess(api=api, container_id="cid-123").Popen(
        "echo hi",
        cwd="/workspace",
        interactive=True,
    )
    assert proc.pid == 123
    assert api.exec_create_calls[0]["tty"] is True


def test_spawn_raises_when_pid_handshake_invalid() -> None:
    fake_api = FakeExecApi([_mux_frame(1, b"not-a-pid\n")])
    with pytest.raises(RuntimeError, match="pid handshake failed"):
        DockerSubprocess(api=fake_api, container_id="cid-123").Popen(
            "echo hi",
            cwd="/workspace",
            env=None,
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
    proc = DockerSubprocess(api=api, container_id="cid-123").Popen(
        "echo hi",
        cwd="/workspace",
        env=None,
    )

    stdout, stderr = proc.communicate(timeout=1)
    assert stdout == b"out"
    assert stderr == b"err"


def test_communicate_timeout_raises() -> None:
    api = FakeExecApi([_mux_frame(1, b"123\n")], inspect_sequence=[None, None, None])
    proc = DockerSubprocess(api=api, container_id="cid-123").Popen(
        "sleep 10",
        cwd="/workspace",
        env=None,
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
    proc = DockerSubprocess(api=api, container_id="cid-123").Popen(
        "cat",
        cwd="/workspace",
        env=None,
    )

    stdout, stderr = proc.communicate(input=b"hello", timeout=1)
    assert stdout == b"out"
    assert stderr == b""
    assert api.last_socket is not None
    assert api.last_socket.sent == [b"hello"]
    assert api.last_socket.shutdown_called is True


def test_capture_stream_handles_and_context_manager() -> None:
    api = FakeExecApi(
        [
            _mux_frame(1, b"123\n"),
            _mux_frame(2, b"err"),
            _mux_frame(1, b"out"),
        ],
        inspect_sequence=[None, 0],
    )
    with DockerSubprocess(api=api, container_id="cid-123").Popen(
        "cat",
        cwd="/workspace",
        env=None,
    ) as proc:
        assert proc.stdin is not None
        proc.stdin.write(b"ping")
        proc.stdin.close()
        stdout, stderr = proc.communicate(timeout=1)
        assert stdout == b"out"
        assert stderr == b"err"

    assert proc.stdin is None
    assert proc.stdout is None
    assert proc.stderr is None


def test_signal_methods_issue_kill_exec_calls() -> None:
    api = FakeExecApi([_mux_frame(1, b"123\n")], inspect_sequence=[0])
    proc = DockerSubprocess(api=api, container_id="cid-123").Popen(
        "echo hi",
        cwd="/workspace",
        env=None,
    )

    proc.terminate()
    proc.kill()
    signal_cmds = [
        call.get("cmd")
        for call in api.exec_create_calls
        if isinstance(call.get("cmd"), list) and call.get("cmd", [None])[0] == "sh"
    ]
    assert [
        "sh",
        "-lc",
        "kill -TERM -123 2>/dev/null || kill -TERM 123",
    ] in signal_cmds
    assert [
        "sh",
        "-lc",
        "kill -KILL -123 2>/dev/null || kill -KILL 123",
    ] in signal_cmds


def test_send_signal_rejects_unsupported_signal() -> None:
    api = FakeExecApi([_mux_frame(1, b"123\n")], inspect_sequence=[0])
    proc = DockerSubprocess(api=api, container_id="cid-123").Popen(
        "echo hi",
        cwd="/workspace",
        env=None,
    )

    with pytest.raises(ValueError, match="unsupported signal"):
        proc.send_signal("USR1")
    kill_cmds = [
        call.get("cmd")
        for call in api.exec_create_calls
        if isinstance(call.get("cmd"), list)
        and call.get("cmd", [None])[0] == "sh"
        and len(call.get("cmd", [])) >= 3
        and "kill " in str(call.get("cmd", ["", "", ""])[2])
    ]
    assert kill_cmds == []


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

    signal_cmds = [
        call.get("cmd")
        for call in api.exec_create_calls
        if isinstance(call.get("cmd"), list) and call.get("cmd", [None])[0] == "sh"
    ]
    assert [
        "sh",
        "-lc",
        "kill -TERM -123 2>/dev/null || kill -TERM 123",
    ] in signal_cmds
    assert [
        "sh",
        "-lc",
        "kill -KILL -123 2>/dev/null || kill -KILL 123",
    ] in signal_cmds


def test_exec_non_zero_returns_result_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()

    class FakeHandle:
        def __init__(self) -> None:
            self.stdin = None
            out_r, out_w = os.pipe()
            err_r, err_w = os.pipe()
            os.write(err_w, b"boom")
            os.close(out_w)
            os.close(err_w)
            self.stdout = os.fdopen(out_r, "rb", buffering=0)
            self.stderr = os.fdopen(err_r, "rb", buffering=0)
            self.returncode: int | None = 7

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            return 7

        def poll(self) -> int | None:
            return 7

        def terminate(self) -> None:
            return

        def kill(self) -> None:
            return

        def resize(self, rows: int, cols: int) -> None:
            _ = (rows, cols)

        def __enter__(self):
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            self.stdout.close()
            self.stderr.close()
            return

    monkeypatch.setattr(
        backend,
        "exec",
        lambda *args, **kwargs: FakeHandle(),
    )

    result = exec_capture(backend, "exit 7", cwd="/workspace")

    assert result.exit_code == 7
    assert result.stderr == "boom"


def test_run_fg_raises_runtime_error_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    api = FakeExecApi(
        [_mux_frame(1, b"123\n")],
        inspect_sequence=[None, 7],
    )
    subproc = DockerSubprocess(api=api, container_id="cid-123")
    with pytest.raises(RuntimeError, match="exit code 7"):
        subproc.run_fg("exit 7", cwd="/workspace")


def test_exec_fg_raises_runtime_error_on_non_zero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())

    class FakeHandle:
        def __init__(self) -> None:
            self.stdin = io.BytesIO()
            out_r, out_w = os.pipe()
            err_r, err_w = os.pipe()
            os.close(out_w)
            os.close(err_w)
            self.stdout = os.fdopen(out_r, "rb", buffering=0)
            self.stderr = os.fdopen(err_r, "rb", buffering=0)
            self.returncode: int | None = 7

        def wait(self, timeout: float | None = None) -> int:
            _ = timeout
            return 7

        def poll(self) -> int | None:
            return 7

        def terminate(self) -> None:
            return

        def kill(self) -> None:
            return

        def resize(self, rows: int, cols: int) -> None:
            _ = (rows, cols)

        def __enter__(self):
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            self.stdout.close()
            self.stderr.close()
            return

    monkeypatch.setattr(backend, "exec", lambda *args, **kwargs: FakeHandle())
    with pytest.raises(RuntimeError, match="exit code 7"):
        exec_fg(backend, "exit 7", cwd="/workspace")


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
    assert len(fake_container.exec_run_calls) == 1
    cmd, kwargs = fake_container.exec_run_calls[0]
    assert cmd == ["chown", "-R", "1000:1000", "/work/a.txt"]
    assert kwargs.get("user") == "0"


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
