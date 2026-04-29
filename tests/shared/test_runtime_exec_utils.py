import io
import os

from saddler.runtime.backend import exec_bg, exec_capture, exec_fg


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


def test_exec_bg_calls_detach_mode() -> None:
    backend = _FakeBackend()
    exec_bg(backend, "sleep 10", cwd="/workspace")
    assert backend.last_exec_kwargs is not None
    assert backend.last_exec_kwargs["detach"] is True
