import os
import select
import sys
from pathlib import Path

import pytest

from saddler.infra.runtime.local import (
    LocalPipeHandle,
    LocalPtyHandle,
    LocalRuntimeBackend,
)
from saddler.runtime.backend import exec_capture, exec_fg
from saddler.runtime.model import RuntimeSpec


def _make_backend() -> LocalRuntimeBackend:
    return LocalRuntimeBackend(spec=RuntimeSpec(backend_type="local"))


def test_exec_supports_shell_features_for_string_command() -> None:
    backend = _make_backend()

    result = exec_capture(backend, "printf 'alpha\\nbeta\\n' | wc -l", cwd=".")

    assert result.exit_code == 0
    assert result.stdout.strip() == "2"


def test_exec_supports_shell_semantics_for_list_command() -> None:
    backend = _make_backend()

    result = exec_capture(backend, ["echo", "foo | tr o a"], cwd=".")

    assert result.exit_code == 0
    assert result.stdout.strip() == "foo | tr o a"


def test_exec_fg_raises_runtime_error_on_non_zero_exit() -> None:
    backend = _make_backend()

    try:
        exec_fg(backend, "exit 7", cwd=".")
        raise AssertionError("exec_fg should raise on non-zero exit")
    except RuntimeError as exc:
        assert "exit code 7" in str(exc)


def test_local_pipe_handle_stdin_stdout_roundtrip() -> None:
    backend = _make_backend()

    with backend.exec("cat", cwd=".", stdin=True, stdout=True, stderr=False) as handle:
        assert isinstance(handle, LocalPipeHandle)
        assert handle.stdin is not None
        assert handle.stdout is not None
        handle.stdin.write(b"hello\n")
        handle.stdin.flush()
        handle.stdin.close()
        data = handle.stdout.read()
        handle.wait(timeout=2.0)

    assert data == b"hello\n"


def test_local_pipe_handle_terminate_kills_process() -> None:
    backend = _make_backend()

    with backend.exec(
        "sleep 60", cwd=".", stdin=False, stdout=False, stderr=False
    ) as handle:
        assert isinstance(handle, LocalPipeHandle)
        assert handle.poll() is None
        handle.terminate()
        code = handle.wait(timeout=2.0)

    assert code != 0


def test_local_pty_handle_returned_for_tty_exec() -> None:
    backend = _make_backend()

    try:
        with backend.exec("true", cwd=".", tty=True) as handle:
            assert isinstance(handle, LocalPtyHandle)
            assert handle.stdout is not None
            assert handle.stdout.fileno() >= 0
            handle.wait(timeout=2.0)
    except OSError as exc:
        pytest.skip(f"PTY unavailable in this environment: {exc}")


def test_local_pty_handle_child_sees_tty(tmp_path: Path) -> None:
    result_file = tmp_path / "result.txt"
    backend = _make_backend()
    cmd = (
        f"{sys.executable} -c "
        f"\"import sys; open({str(result_file)!r}, 'w').write(str(sys.stdout.isatty()))\""
    )

    try:
        with backend.exec(cmd, cwd=".", tty=True) as handle:
            assert isinstance(handle, LocalPtyHandle)
            handle.wait(timeout=5.0)
            while True:
                r, _, _ = select.select([handle.stdout], [], [], 0.05)
                if not r:
                    break
                try:
                    chunk = os.read(handle.stdout.fileno(), 256)
                except OSError:
                    break
                if not chunk:
                    break
    except OSError as exc:
        pytest.skip(f"PTY unavailable in this environment: {exc}")

    assert result_file.read_text().strip() == "True"
