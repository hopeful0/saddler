import io
import os
import sys

from saddler.runtime.backend import exec_bg, exec_capture, exec_fg, pump_fg


class _FakeHandle:
    def __init__(
        self, *, stdout: bytes = b"", stderr: bytes = b"", exit_code: int = 0
    ) -> None:
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


class _FakeBackend:
    def __init__(self) -> None:
        self.last_exec_kwargs: dict | None = None

    def exec(self, command, cwd, env=None, **kwargs):  # noqa: ANN001
        self.last_exec_kwargs = {"command": command, "cwd": cwd, "env": env, **kwargs}
        detach = bool(kwargs.get("detach"))
        if detach:
            return None
        if command == "err":
            return _FakeHandle(stderr=b"boom\n", exit_code=0)
        if command == "bad":
            return _FakeHandle(stdout=b"", stderr=b"", exit_code=7)
        return _FakeHandle(stdout=b"ok\n", stderr=b"", exit_code=0)


class _Sink:
    def __init__(self) -> None:
        self.data = b""
        self.closed = False

    def write(self, data: bytes) -> int:
        self.data += data
        return len(data)

    def flush(self) -> None:
        return

    def close(self) -> None:
        self.closed = True


class _PipeStdin:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd

    def isatty(self) -> bool:
        return False


class _TtyStdin:
    def isatty(self) -> bool:
        return True


def test_exec_capture_collects_stdout_and_stderr() -> None:
    backend = _FakeBackend()
    out = exec_capture(backend, "err", cwd="/workspace")
    assert out.exit_code == 0
    assert out.stdout == ""
    assert out.stderr == "boom\n"


def test_exec_fg_raises_on_non_zero(monkeypatch) -> None:  # noqa: ANN001
    backend = _FakeBackend()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    try:
        exec_fg(backend, "bad", cwd="/workspace")
        raise AssertionError("exec_fg should raise on non-zero exit")
    except RuntimeError as exc:
        assert "exit code 7" in str(exc)


def test_exec_fg_delegates_to_backend_exec_and_pump_fg(monkeypatch) -> None:  # noqa: ANN001
    class _Backend:
        def __init__(self, handle) -> None:  # noqa: ANN001
            self.handle = handle
            self.exec_calls: list[dict] = []

        def exec(self, command, cwd, env=None, **kwargs):  # noqa: ANN001
            self.exec_calls.append(
                {"command": command, "cwd": cwd, "env": env, **kwargs}
            )
            return self.handle

    handle = _FakeHandle(stdout=b"", stderr=b"", exit_code=0)
    backend = _Backend(handle)
    pump_calls: list[dict] = []

    def _fake_pump(proc, tty):  # noqa: ANN001
        pump_calls.append({"proc": proc, "tty": tty})

    monkeypatch.setattr("sys.stdin", _TtyStdin())
    monkeypatch.setattr("saddler.runtime.backend.pump_fg", _fake_pump)

    exec_fg(backend, "echo hi", cwd="/workspace", env={"K": "V"})

    assert backend.exec_calls == [
        {
            "command": "echo hi",
            "cwd": "/workspace",
            "env": {"K": "V"},
            "stdin": True,
            "stdout": True,
            "stderr": False,
            "tty": True,
            "detach": False,
        }
    ]
    assert pump_calls == [{"proc": handle, "tty": True}]


def test_pump_fg_forwards_pipe_stdin_stdout_stderr_when_not_tty(
    monkeypatch,
) -> None:  # noqa: ANN001
    handle = _FakeHandle(stdout=b"ok\n", stderr=b"boom\n", exit_code=0)
    sink = _Sink()
    handle.stdin = sink
    stdin_r, stdin_w = os.pipe()
    os.write(stdin_w, b"input\n")
    os.close(stdin_w)

    monkeypatch.setattr("sys.stdin", _PipeStdin(stdin_r))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    try:
        pump_fg(handle, tty=False)
        assert handle.stdin is not None
        assert sink.data == b"input\n"
        assert sink.closed is True
        assert sys.stdout.getvalue() == "ok\n"
        assert sys.stderr.getvalue() == "boom\n"
    finally:
        os.close(stdin_r)


def test_pump_fg_raises_on_non_zero_exit(monkeypatch) -> None:  # noqa: ANN001
    handle = _FakeHandle(stdout=b"", stderr=b"", exit_code=7)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())

    try:
        pump_fg(handle, tty=False)
        raise AssertionError("pump_fg should raise on non-zero exit")
    except RuntimeError as exc:
        assert "exit code 7" in str(exc)


def test_pump_fg_raises_when_missing_interactive_streams(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    monkeypatch.setattr("sys.stderr", io.StringIO())

    missing_stdin = _FakeHandle(stdout=b"", stderr=b"", exit_code=0)
    missing_stdin.stdin = None
    try:
        pump_fg(missing_stdin, tty=False)
        raise AssertionError("pump_fg should require stdin/stdout")
    except RuntimeError as exc:
        assert str(exc) == "interactive exec requires stdin/stdout"

    missing_stdout = _FakeHandle(stdout=b"", stderr=b"", exit_code=0)
    missing_stdout.stdout = None
    try:
        pump_fg(missing_stdout, tty=False)
        raise AssertionError("pump_fg should require stdin/stdout")
    except RuntimeError as exc:
        assert str(exc) == "interactive exec requires stdin/stdout"


def test_exec_bg_calls_detach_mode() -> None:
    backend = _FakeBackend()
    exec_bg(backend, "sleep 10", cwd="/workspace")
    assert backend.last_exec_kwargs is not None
    assert backend.last_exec_kwargs["detach"] is True
