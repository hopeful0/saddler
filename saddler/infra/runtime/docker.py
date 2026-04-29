from __future__ import annotations

import io
import os
import select
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import IO, Any, Literal, Self

import docker
from docker.api.client import APIClient
from docker.client import DockerClient
from docker.errors import DockerException, NotFound
from pydantic import BaseModel, Field, JsonValue

from ...runtime.backend import (
    Command,
    ProcessHandle,
    normalize_shell_command,
    register_runtime_backend,
)
from ...runtime.model import RuntimeHostBindMount, RuntimeMountType, RuntimeSpec


class DockerRuntimeSpec(BaseModel):
    image: str = Field(default="python:3.12-slim", min_length=1)
    container_name: str | None = None
    command: list[str] = Field(default_factory=lambda: ["sleep", "infinity"])
    user: str | None = None

    @classmethod
    def from_runtime_spec(cls, spec: RuntimeSpec) -> Self:
        backend_spec = spec.backend_spec
        if backend_spec is None:
            return cls()
        if not isinstance(backend_spec, dict):
            raise ValueError("runtime backend_spec must be a JSON object")
        return cls.model_validate(backend_spec)


class DockerRuntimeState(BaseModel):
    container_id: str | None = None
    container_name: str | None = None
    image: str | None = None


class DockerPopen:
    """Low-level handle for a running docker exec session.

    Knows about the exec socket and mux/tty framing protocol.
    Does not touch sys.stdin / sys.stdout — I/O routing is the caller's job.
    """

    _ALLOWED_SIGNALS = frozenset({"TERM", "KILL", "INT", "HUP", "QUIT"})

    def __init__(
        self,
        *,
        api: APIClient,
        exec_id: str,
        container_id: str,
        pid: int,
        io_socket: object,
        mode: Literal["mux", "tty"],
        initial_stdout: list[bytes] | None = None,
        initial_stderr: list[bytes] | None = None,
        args: Command | None = None,
    ) -> None:
        self._api = api
        self._exec_id = exec_id
        self._container_id = container_id
        self._socket = io_socket
        self._mode = mode
        self._initial_stdout: list[bytes] = initial_stdout or []
        self._initial_stderr: list[bytes] = initial_stderr or []
        self.args = args
        self.pid = pid
        self.returncode: int | None = None
        self._closed = False
        self._stdin_closed = False
        self.stdin: _DockerSocketWriter = _DockerSocketWriter(self)
        self.stdout: None = None
        self.stderr: None = None

    # ------------------------------------------------------------------
    # Public subprocess-compatible interface
    # ------------------------------------------------------------------

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, bytes]:
        """Send *input* bytes to the process and collect its output.

        Returns (stdout_bytes, stderr_bytes).  Always raw bytes; decoding is
        the caller's responsibility.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        if input is not None:
            _socket_send(self._socket, input)
        self._close_stdin()
        if self._mode == "mux":
            return self._collect_mux(deadline, timeout)
        return self._collect_tty(deadline, timeout)

    def poll(self) -> int | None:
        details = self._api.exec_inspect(self._exec_id)
        exit_code = details.get("ExitCode")
        self.returncode = None if exit_code is None else int(exit_code)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            details = self._api.exec_inspect(self._exec_id)
            exit_code = details.get("ExitCode")
            if exit_code is not None:
                self.returncode = int(exit_code)
                return self.returncode
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args or [], timeout or 0)
            time.sleep(0.02)

    def send_signal(self, sig: int | str) -> None:
        if isinstance(sig, int):
            sig_name = signal.Signals(sig).name
            sig_name = sig_name.removeprefix("SIG")
        else:
            sig_name = sig.removeprefix("SIG").upper()
        sig_name = self._normalize_signal_name(sig_name)
        self._signal_process(sig_name)

    def terminate(self) -> None:
        self.send_signal("TERM")

    def kill(self) -> None:
        self.send_signal("KILL")

    def forward_tty(self) -> None:
        """Forward the current terminal's stdin/stdout to the container exec."""
        import termios
        import tty

        sock = self._socket
        tty_stdout = sys.stdout
        stdout_buf = tty_stdout.buffer if hasattr(tty_stdout, "buffer") else None
        stdin_fd: int | None = None
        try:
            stdin_fd = sys.stdin.fileno() if sys.stdin else None
        except (AttributeError, io.UnsupportedOperation):
            stdin_fd = None
        original_tty = None
        old_winch_handler = None

        def write_stdout(chunk: bytes) -> None:
            if stdout_buf is not None:
                stdout_buf.write(chunk)
                stdout_buf.flush()
                return
            sys.stdout.write(chunk.decode(errors="replace"))
            sys.stdout.flush()

        def resize_exec() -> None:
            if not sys.stdout or not sys.stdout.isatty():
                return
            cols, lines = shutil.get_terminal_size(fallback=(80, 24))
            try:
                self._api.exec_resize(self._exec_id, height=lines, width=cols)
            except Exception:
                return

        try:
            if stdin_fd is not None:
                original_tty = termios.tcgetattr(stdin_fd)
                tty.setraw(stdin_fd)
            if hasattr(signal, "SIGWINCH"):
                old_winch_handler = signal.getsignal(signal.SIGWINCH)
                signal.signal(signal.SIGWINCH, lambda *_: resize_exec())
                resize_exec()

            while True:
                exit_code = self.poll()
                read_targets: list[object] = [sock]
                if stdin_fd is not None:
                    read_targets.append(stdin_fd)
                ready, _, _ = select.select(read_targets, [], [], 0.05)

                if sock in ready:
                    chunk = _socket_recv(sock, 4096)
                    if chunk:
                        write_stdout(chunk)

                if stdin_fd is not None and stdin_fd in ready:
                    data = os.read(stdin_fd, 4096)
                    if data:
                        _socket_send(sock, data)
                    else:
                        stdin_fd = None

                if exit_code is not None:
                    # Best-effort drain to reduce trailing output loss near exit.
                    for _ in range(8):
                        more_ready, _, _ = select.select([sock], [], [], 0)
                        if sock not in more_ready:
                            break
                        tail = _socket_recv(sock, 4096)
                        if not tail:
                            break
                        write_stdout(tail)
                    return
        finally:
            if stdin_fd is not None and original_tty is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_tty)
            if hasattr(signal, "SIGWINCH") and old_winch_handler is not None:
                signal.signal(signal.SIGWINCH, old_winch_handler)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_stdin()
        if self.returncode is None and self.poll() is None:
            try:
                self.terminate()
                time.sleep(0.05)
                if self.poll() is None:
                    self.kill()
            except Exception:
                pass
        if self.stdin is not None:
            try:
                self.stdin.close()
            except Exception:
                pass
        self.stdin = None  # type: ignore[assignment]
        if hasattr(self._socket, "close"):
            self._socket.close()  # type: ignore[attr-defined]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Factory constructors — one per Docker exec socket protocol
    # ------------------------------------------------------------------

    @classmethod
    def from_tty_exec(
        cls,
        *,
        api: APIClient,
        exec_id: str,
        container_id: str,
        io_socket: object,
        args: Command | None = None,
    ) -> Self:
        stream_iter = _socket_chunk_iter(io_socket)
        pid_line, buffered = _read_pid_and_buffer(stream_iter)
        pid = _parse_pid_line(pid_line)
        return cls(
            api=api,
            exec_id=exec_id,
            container_id=container_id,
            pid=pid,
            io_socket=io_socket,
            mode="tty",
            initial_stdout=buffered,
            args=args,
        )

    @classmethod
    def from_mux_exec(
        cls,
        *,
        api: APIClient,
        exec_id: str,
        container_id: str,
        io_socket: object,
        args: Command | None = None,
    ) -> Self:
        pid_line, initial_stdout, initial_stderr = _read_pid_and_buffer_mux(
            io_socket, timeout=5.0
        )
        pid = _parse_pid_line(pid_line)
        return cls(
            api=api,
            exec_id=exec_id,
            container_id=container_id,
            pid=pid,
            io_socket=io_socket,
            mode="mux",
            initial_stdout=initial_stdout,
            initial_stderr=initial_stderr,
            args=args,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_mux(
        self,
        deadline: float | None,
        timeout: float | None,
    ) -> tuple[bytes, bytes]:
        stdout_chunks: list[bytes] = list(self._initial_stdout)
        stderr_chunks: list[bytes] = list(self._initial_stderr)

        # Phase 1: drain the multiplexed stream until socket EOF.
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args or [], timeout or 0)
            try:
                frame = _read_mux_frame(self._socket, deadline)
            except TimeoutError:
                raise subprocess.TimeoutExpired(self.args or [], timeout or 0)
            if frame is None:
                break
            stream_type, payload = frame
            if stream_type == 1:
                stdout_chunks.append(payload)
            elif stream_type == 2:
                stderr_chunks.append(payload)

        # Phase 2: wait for the process exit code (socket closed means data done).
        while True:
            if self.poll() is not None:
                return b"".join(stdout_chunks), b"".join(stderr_chunks)
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args or [], timeout or 0)
            time.sleep(0.01)

    def _collect_tty(
        self,
        deadline: float | None,
        timeout: float | None,
    ) -> tuple[bytes, bytes]:
        chunks: list[bytes] = list(self._initial_stdout)
        for chunk in _socket_chunk_iter(self._socket):
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args or [], timeout or 0)
            chunks.append(chunk)
        return b"".join(chunks), b""

    def _signal_process(self, sig: str) -> None:
        try:
            payload = self._api.exec_create(
                container=self._container_id,
                cmd=[
                    "sh",
                    "-lc",
                    f"kill -{sig} -{self.pid} 2>/dev/null || kill -{sig} {self.pid}",
                ],
                tty=False,
                stdin=False,
            )
            self._api.exec_start(payload["Id"], stream=False, demux=False, tty=False)
        except Exception:
            return

    @classmethod
    def _normalize_signal_name(cls, sig_name: str) -> str:
        if sig_name not in cls._ALLOWED_SIGNALS:
            raise ValueError(f"unsupported signal: {sig_name}")
        return sig_name

    def _close_stdin(self) -> None:
        if self._stdin_closed:
            return
        self._stdin_closed = True
        try:
            if hasattr(self._socket, "shutdown"):
                self._socket.shutdown(socket.SHUT_WR)  # type: ignore[attr-defined]
        except Exception:
            return


class DockerSubprocess:
    """Subprocess-like facade over Docker exec.

    Owns I/O routing decisions: when to use TTY protocol, how to decode output,
    how to forward stdin/stdout to the terminal.
    """

    def __init__(
        self, *, api: APIClient, container_id: str, user: str | None = None
    ) -> None:
        self._api = api
        self._container_id = container_id
        self._user = user

    def Popen(
        self,
        command: Command,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        interactive: bool = False,
    ) -> DockerPopen:
        """Open a docker exec session.

        Use interactive=True only when both sys.stdin and sys.stdout are ttys;
        the caller is responsible for checking.  interactive=True uses the Docker
        TTY protocol; False uses the multiplexed (demux) protocol.
        """
        cmd_str = normalize_shell_command(command)
        wrapper = self._build_shell_wrapper(cmd_str)
        payload = self._api.exec_create(
            container=self._container_id,
            cmd=["sh", "-lc", wrapper],
            workdir=cwd,
            environment=env or None,
            tty=interactive,
            stdin=True,
            user=self._user or "",
        )
        exec_id = payload["Id"]
        raw_sock = self._api.exec_start(exec_id, socket=True, tty=interactive)
        io_socket = getattr(raw_sock, "_sock", raw_sock)
        factory = (
            DockerPopen.from_tty_exec if interactive else DockerPopen.from_mux_exec
        )
        return factory(
            api=self._api,
            exec_id=exec_id,
            container_id=self._container_id,
            io_socket=io_socket,
            args=command,
        )

    def run(
        self,
        command: Command,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        input: str | bytes | None = None,
        timeout: float | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        input_bytes = input.encode() if isinstance(input, str) else input
        with self.Popen(command, cwd=cwd, env=env, interactive=False) as proc:
            try:
                stdout_b, stderr_b = proc.communicate(
                    input=input_bytes, timeout=timeout
                )
            except subprocess.TimeoutExpired:
                _cleanup_timed_out_process(proc)
                raise
        returncode = proc.returncode  # set by _collect_mux poll loop
        stdout = stdout_b.decode(errors="replace")
        stderr = stderr_b.decode(errors="replace")
        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode, command, output=stdout, stderr=stderr
            )
        return subprocess.CompletedProcess(
            args=command, returncode=returncode, stdout=stdout, stderr=stderr
        )

    def run_fg(
        self,
        command: Command,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        interactive = bool(sys.stdin and sys.stdin.isatty())
        with self.Popen(command, cwd=cwd, env=env, interactive=interactive) as proc:
            try:
                if interactive:
                    proc.forward_tty()
                    exit_code = proc.wait()
                else:
                    stdin_data = _read_stdin_bytes()
                    stdout_b, stderr_b = proc.communicate(input=stdin_data)
                    _write_bytes_to_fd(sys.stdout, stdout_b)
                    _write_bytes_to_fd(sys.stderr, stderr_b)
                    exit_code = proc.returncode
            except BaseException:
                _cleanup_timed_out_process(proc)
                raise
        if exit_code != 0:
            raise RuntimeError(f"docker exec failed with exit code {exit_code}")

    @staticmethod
    def _build_shell_wrapper(command: str) -> str:
        return f"echo $$; exec sh -lc {shlex.quote(command)}"


class _PipeReader(io.RawIOBase):
    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._closed = False

    def fileno(self) -> int:
        return self._fd

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:  # type: ignore[override]
        if self._closed:
            return b""
        if size <= 0:
            size = 65536
        return os.read(self._fd, size)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self._fd)


class DockerTtyHandle:
    def __init__(self, proc: DockerPopen, api: APIClient, exec_id: str) -> None:
        self._proc = proc
        self._api = api
        self._exec_id = exec_id
        self.stdin = proc.stdin
        self.stdout = proc._socket  # noqa: SLF001
        self.stderr = None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self._proc.wait(timeout=timeout)

    def poll(self) -> int | None:
        return self._proc.poll()

    def terminate(self) -> None:
        self._proc.terminate()

    def kill(self) -> None:
        self._proc.kill()

    def resize(self, rows: int, cols: int) -> None:
        self._api.exec_resize(self._exec_id, height=rows, width=cols)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._proc.close()


class DockerMuxHandle:
    def __init__(self, proc: DockerPopen) -> None:
        self._proc = proc
        self.stdin = proc.stdin
        self._sock = proc._socket  # noqa: SLF001
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        self._out_w = out_w
        self._err_w = err_w
        self.stdout = _PipeReader(out_r)
        self.stderr = _PipeReader(err_r)
        self._pump_done = threading.Event()
        self._pump = threading.Thread(target=self._pump_mux, daemon=True)
        self._pump.start()

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    def _pump_mux(self) -> None:
        try:
            while True:
                frame = _read_mux_frame(self._sock, None)
                if frame is None:
                    break
                stream_type, payload = frame
                if stream_type == 1:
                    os.write(self._out_w, payload)
                elif stream_type == 2:
                    os.write(self._err_w, payload)
        finally:
            for fd in (self._out_w, self._err_w):
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._pump_done.set()

    def wait(self, timeout: float | None = None) -> int:
        return self._proc.wait(timeout=timeout)

    def poll(self) -> int | None:
        return self._proc.poll()

    def terminate(self) -> None:
        self._proc.terminate()

    def kill(self) -> None:
        self._proc.kill()

    def resize(self, rows: int, cols: int) -> None:
        _ = (rows, cols)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._pump.join(timeout=0.5)
        self._proc.close()
        self.stdout.close()
        self.stderr.close()


# ---------------------------------------------------------------------------
# Module-level I/O helpers used by DockerSubprocess
# ---------------------------------------------------------------------------


def _cleanup_timed_out_process(proc: DockerPopen) -> None:
    proc.terminate()
    time.sleep(0.05)
    if proc.poll() is None:
        proc.kill()


def _read_stdin_bytes() -> bytes | None:
    if not sys.stdin or sys.stdin.isatty():
        return None
    if hasattr(sys.stdin, "buffer"):
        return sys.stdin.buffer.read()
    return sys.stdin.read().encode()


def _write_bytes_to_fd(stream: IO[Any], data: bytes) -> None:
    if not data:
        return
    if hasattr(stream, "buffer"):
        stream.buffer.write(data)  # type: ignore[attr-defined]
        stream.buffer.flush()  # type: ignore[attr-defined]
    else:
        stream.write(data.decode(errors="replace"))  # type: ignore[attr-defined]
        if hasattr(stream, "flush"):
            stream.flush()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Internal I/O adapters
# ---------------------------------------------------------------------------


class _DockerSocketWriter(io.BufferedWriter):
    def __init__(self, proc: DockerPopen) -> None:
        super().__init__(io.BytesIO())
        self._proc = proc

    def writable(self) -> bool:
        return True

    def write(self, b: bytes) -> int:  # type: ignore[override]
        if not b:
            return 0
        _socket_send(self._proc._socket, b)  # noqa: SLF001
        return len(b)

    def close(self) -> None:
        self._proc._close_stdin()  # noqa: SLF001
        super().close()


# ---------------------------------------------------------------------------
# Socket / stream primitives
# ---------------------------------------------------------------------------


def _read_pid_and_buffer(
    stream: Iterator[bytes],
) -> tuple[str, list[bytes]]:
    buffered = bytearray()
    for chunk in stream:
        if not chunk:
            continue
        data = chunk if isinstance(chunk, bytes) else bytes(chunk)
        newline_at = data.find(b"\n")
        if newline_at >= 0:
            buffered.extend(data[:newline_at])
            remaining = data[newline_at + 1 :]
            leftovers = [remaining] if remaining else []
            return buffered.decode(errors="replace"), leftovers
        buffered.extend(data)
    raise RuntimeError("docker exec pid handshake failed: missing pid line")


def _parse_pid_line(line: str) -> int:
    text = line.strip()
    if not text.isdigit():
        raise RuntimeError(f"docker exec pid handshake failed: invalid pid '{line}'")
    return int(text)


def _remaining_timeout(
    deadline: float | None, *, cap: float | None = None
) -> float | None:
    if deadline is None:
        return cap
    remaining = max(0.0, deadline - time.monotonic())
    if cap is None:
        return remaining
    return min(remaining, cap)


def _read_exact(sock: object, size: int, deadline: float | None) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    while total < size:
        if hasattr(sock, "fileno"):
            wait_s = _remaining_timeout(deadline, cap=0.05)
            if deadline is not None and wait_s is not None and wait_s <= 0:
                raise TimeoutError("docker exec timed out")
            ready, _, _ = select.select([sock], [], [], wait_s)
            if sock not in ready:
                continue
        elif deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("docker exec timed out")
        chunk = _socket_recv(sock, size - total)
        if not chunk:
            if total == 0:
                return None
            raise RuntimeError("unexpected EOF while reading docker exec stream")
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _read_mux_frame(sock: object, deadline: float | None) -> tuple[int, bytes] | None:
    header = _read_exact(sock, 8, deadline)
    if header is None:
        return None
    stream_type = header[0]
    size = int.from_bytes(header[4:], byteorder="big")
    payload = _read_exact(sock, size, deadline) or b""
    return stream_type, payload


def _read_pid_and_buffer_mux(
    sock: object, *, timeout: float
) -> tuple[str, list[bytes], list[bytes]]:
    deadline = time.monotonic() + timeout
    stdout_buf = bytearray()
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    while True:
        frame = _read_mux_frame(sock, deadline)
        if frame is None:
            raise RuntimeError("docker exec pid handshake failed: missing pid line")
        stream_type, payload = frame
        if stream_type == 2:
            stderr_chunks.append(payload)
            continue
        if stream_type != 1:
            continue
        stdout_buf.extend(payload)
        newline_at = stdout_buf.find(b"\n")
        if newline_at >= 0:
            pid_line = stdout_buf[:newline_at].decode(errors="replace")
            remaining = bytes(stdout_buf[newline_at + 1 :])
            if remaining:
                stdout_chunks.append(remaining)
            return pid_line, stdout_chunks, stderr_chunks


def _socket_recv(sock: object, size: int) -> bytes:
    if hasattr(sock, "recv"):
        data = sock.recv(size)  # type: ignore[attr-defined]
    else:
        data = sock.read(size)  # type: ignore[attr-defined]
    if not data:
        return b""
    return data if isinstance(data, bytes) else bytes(data)


def _socket_send(sock: object, data: bytes) -> None:
    if hasattr(sock, "sendall"):
        sock.sendall(data)  # type: ignore[attr-defined]
        return
    sock.write(data)  # type: ignore[attr-defined]


def _socket_chunk_iter(sock: object, chunk_size: int = 4096) -> Iterator[bytes]:
    while True:
        chunk = _socket_recv(sock, chunk_size)
        if not chunk:
            return
        yield chunk


class _IterStream(io.RawIOBase):
    """Adapt a bytes-chunk iterable to a readable file-like object for tarfile streaming."""

    def __init__(self, iterable: Iterable[bytes]) -> None:
        self._iter = iter(iterable)
        self._buf = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        while not self._buf:
            try:
                chunk = next(self._iter)
                self._buf = chunk if isinstance(chunk, bytes) else bytes(chunk)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def _check_member_path(name: str) -> None:
    """Raise if *name* could escape the extraction root (tar-slip prevention)."""
    p = Path(name)
    if p.is_absolute() or ".." in p.parts:
        raise RuntimeError(
            f"archive member {name!r} would escape destination directory"
        )


def _build_put_archive_stream(
    src_host: str, dest_runtime: str
) -> tuple[str, io.BytesIO]:
    dest_path = Path(dest_runtime)
    if not dest_path.is_absolute():
        raise ValueError(f"dest_runtime must be an absolute path: {dest_runtime!r}")

    src_path = Path(src_host)
    if not src_path.exists():
        raise RuntimeError(f"Source path does not exist: {src_host}")

    # Docker Engine: put_archive `path` must already exist in the container; the
    # archive root member is extracted as a child under that path. Use the parent
    # of dest + basename(dest) so e.g. copy_to(..., "/skills/docx") works after
    # `rm -rf /skills/docx` as long as `/skills` exists (same as file copy).
    put_path = str(dest_path.parent)
    arcname = dest_path.name

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        tar.add(src_path, arcname=arcname)
    tar_buffer.seek(0)
    return put_path, tar_buffer


def _extract_archive_to_host(
    archive_stream: Iterable[bytes],
    *,
    src_runtime: str,
    dest_host: str,
    stat: dict[str, object] | None,
) -> None:
    src_name = (
        Path(str(stat.get("name"))) if stat and stat.get("name") else Path(src_runtime)
    )
    src_is_dir = bool(stat and stat.get("mode") and int(stat["mode"]) & 0o040000)
    dest_path = Path(dest_host)

    _extract_kw: dict[str, object] = {"set_attrs": False}
    if sys.version_info >= (3, 12):
        _extract_kw["filter"] = "data"

    with tarfile.open(fileobj=_IterStream(archive_stream), mode="r|*") as tar:
        if src_is_dir:
            dest_path.mkdir(parents=True, exist_ok=True)
            for member in tar:
                _check_member_path(member.name)
                if member.issym():
                    _check_member_path(member.linkname)
                tar.extract(member, path=dest_path, **_extract_kw)
        else:
            if dest_path.exists() and dest_path.is_dir():
                output_path = dest_path / src_name.name
            else:
                output_path = dest_path
                output_path.parent.mkdir(parents=True, exist_ok=True)
            extracted_file = False
            for member in tar:
                _check_member_path(member.name)
                if member.isfile():
                    f = tar.extractfile(member)
                    if f is None:
                        raise RuntimeError(
                            "docker get_archive returned unreadable file"
                        )
                    with output_path.open("wb") as out:
                        shutil.copyfileobj(f, out, length=65536)
                    extracted_file = True
                    break
            if not extracted_file:
                raise RuntimeError("docker get_archive returned empty archive")


@register_runtime_backend("docker")
class DockerRuntimeBackend:
    def __init__(
        self,
        spec: RuntimeSpec,
        docker_spec: DockerRuntimeSpec,
        state: DockerRuntimeState | None = None,
    ) -> None:
        self.spec = spec
        self.docker_spec = docker_spec
        self._lazy_client: DockerClient | None = None
        self.state = state or DockerRuntimeState(
            container_name=docker_spec.container_name,
            image=docker_spec.image,
        )

    @classmethod
    def create(cls, spec: RuntimeSpec) -> Self:
        return cls(spec=spec, docker_spec=DockerRuntimeSpec.from_runtime_spec(spec))

    def start(self) -> None:
        client = self._client()
        if self.state.container_id:
            container = client.containers.get(self.state.container_id)
            container.start()
            return

        container_name = (
            self.docker_spec.container_name or f"saddler-{uuid.uuid4().hex[:12]}"
        )
        environment = dict(self.spec.env)
        mounts: dict[str, dict[str, str]] = {}
        for mount in self.spec.mounts:
            if mount.type != RuntimeMountType.BIND:
                raise RuntimeError(f"Unsupported docker mount type: {mount.type}")
            bind_mount = RuntimeHostBindMount.model_validate(mount)
            mounts[str(bind_mount.source)] = {
                "bind": bind_mount.destination,
                "mode": bind_mount.mode.value,
            }
        container = client.containers.run(
            self.docker_spec.image,
            self.docker_spec.command,
            detach=True,
            name=container_name,
            user=self.docker_spec.user,
            environment=environment,
            volumes=mounts,
            init=True,
        )

        self.state = DockerRuntimeState(
            container_id=container.id,
            container_name=container.name,
            image=self.docker_spec.image,
        )

    def is_running(self) -> bool:
        if not self.state.container_id:
            return False
        try:
            container = self._client().containers.get(self.state.container_id)
        except NotFound:
            return False
        container.reload()
        return bool(container.attrs.get("State", {}).get("Running"))

    def stop(self) -> None:
        if not self.state.container_id:
            return
        container = self._client().containers.get(self.state.container_id)
        container.stop(timeout=10)

    def remove(self) -> None:
        if not self.state.container_id:
            return
        container = self._client().containers.get(self.state.container_id)
        container.remove(force=True)
        self.state = DockerRuntimeState(
            container_name=self.state.container_name,
            image=self.state.image,
        )

    def exec(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
        *,
        stdin: bool = False,
        stdout: bool = False,
        stderr: bool = False,
        tty: bool = False,
        detach: bool = False,
        timeout: float | None = None,
    ) -> ProcessHandle | None:
        _ = timeout
        if detach:
            container = self._client().containers.get(self._require_container_id())
            container.exec_run(
                ["sh", "-lc", normalize_shell_command(command)],
                detach=True,
                workdir=cwd,
                environment=env or None,
                user=self.docker_spec.user or "",
            )
            return None
        popen = self._docker_subprocess().Popen(
            command,
            cwd=cwd,
            env=env,
            interactive=tty,
        )
        if tty:
            return DockerTtyHandle(popen, self._client().api, popen._exec_id)  # noqa: SLF001
        handle = DockerMuxHandle(popen)
        if not stdout:
            handle.stdout.close()
            handle.stdout = None  # type: ignore[assignment]
        if not stderr:
            handle.stderr.close()
            handle.stderr = None  # type: ignore[assignment]
        if not stdin and handle.stdin is not None:
            handle.stdin.close()
            handle.stdin = None  # type: ignore[assignment]
        return handle

    def copy_to(self, src_host: str, dest_runtime: str) -> None:
        container = self._client().containers.get(self._require_container_id())
        put_path, archive_stream = _build_put_archive_stream(src_host, dest_runtime)
        ok = container.put_archive(put_path, archive_stream)
        if not ok:
            raise RuntimeError("docker put_archive failed")
        if self.docker_spec.user:
            code, output = container.exec_run(
                ["chown", "-R", self.docker_spec.user, dest_runtime], user="0"
            )
            if code != 0:
                error = (
                    output.decode(errors="replace")
                    if isinstance(output, bytes)
                    else str(output)
                )
                raise RuntimeError(error.strip() or "docker chown failed")

    def copy_from(self, src_runtime: str, dest_host: str) -> None:
        if not Path(src_runtime).is_absolute():
            raise ValueError(f"src_runtime must be an absolute path: {src_runtime!r}")
        cid = self._require_container_id()
        stream, stat = self._client().api.get_archive(cid, src_runtime)
        _extract_archive_to_host(
            stream,
            src_runtime=src_runtime,
            dest_host=dest_host,
            stat=stat,
        )

    @classmethod
    def load_state(cls, spec: RuntimeSpec, state: JsonValue | None) -> Self:
        if state is None:
            parsed_state = None
        else:
            if not isinstance(state, dict):
                raise ValueError("runtime backend_state must be a JSON object")
            parsed_state = DockerRuntimeState.model_validate(state)
        return cls(
            spec=spec,
            docker_spec=DockerRuntimeSpec.from_runtime_spec(spec),
            state=parsed_state,
        )

    def dump_state(self) -> JsonValue | None:
        return self.state.model_dump(mode="json")

    def _require_container_id(self) -> str:
        if not self.state.container_id:
            raise RuntimeError("Docker runtime not started")
        return self.state.container_id

    def _client(self) -> DockerClient:
        if self._lazy_client is None:
            try:
                self._lazy_client = docker.from_env()
            except DockerException as exc:
                raise RuntimeError(
                    f"Failed to initialize Docker client: {exc}"
                ) from exc
        return self._lazy_client

    def _docker_subprocess(self) -> DockerSubprocess:
        return DockerSubprocess(
            api=self._client().api,
            container_id=self._require_container_id(),
            user=self.docker_spec.user,
        )
