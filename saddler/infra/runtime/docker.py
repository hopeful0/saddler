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
import termios
import time
import tty
import uuid
from collections.abc import Iterable, Iterator
from itertools import chain
from pathlib import Path
from typing import IO, Any, Self, TypeAlias

import docker
from docker.api.client import APIClient
from docker.client import DockerClient
from docker.errors import DockerException, NotFound
from pydantic import BaseModel, Field, JsonValue

from ...runtime.backend import (
    Command,
    ExecResult,
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


StdioValue: TypeAlias = int | IO[Any] | None


class DockerPopen:
    def __init__(
        self,
        *,
        api: APIClient,
        exec_id: str,
        container_id: str,
        pid: int,
        socket: object,
        stream: Iterable[bytes] | None = None,
        demux_capture: bool = False,
        initial_stdout: list[bytes] | None = None,
        initial_stderr: list[bytes] | None = None,
        args: Command | None = None,
        stdin: StdioValue = None,
        stdout: StdioValue = None,
        stderr: StdioValue = None,
    ) -> None:
        self._api = api
        self._exec_id = exec_id
        self._container_id = container_id
        self._stream = iter(stream) if stream is not None else None
        self._socket = socket
        self._demux_capture = demux_capture
        self._initial_stdout = initial_stdout or []
        self._initial_stderr = initial_stderr or []
        self.args = args
        self.pid = pid
        self.returncode: int | None = None
        self._forward_stdio = stdin is None and stdout is None and stderr is None
        self._tty_stdin = _resolve_tty_stream(stdin, fallback=sys.stdin)
        self._tty_stdout = _resolve_tty_stream(stdout, fallback=sys.stdout)
        self._interactive_tty = bool(
            self._tty_stdin is not None and self._tty_stdout is not None
        )
        self._capture_stdout = stdout == subprocess.PIPE or stderr == subprocess.STDOUT
        self._capture_stderr = stderr == subprocess.PIPE
        self._merge_stderr_to_stdout = stderr == subprocess.STDOUT
        self._closed = False
        self._stdin_closed = False
        self.stdin: io.BufferedWriter | None = None
        self.stdout: io.BufferedReader | None = None
        self.stderr: io.BufferedReader | None = None
        self._stdin_source: IO[Any] | None = None
        self._stdout_sink: int | IO[Any] | None = None
        self._stderr_sink: int | IO[Any] | None = None
        self._stdout_buf = io.BytesIO(b"".join(self._initial_stdout))
        self._stderr_buf = io.BytesIO(b"".join(self._initial_stderr))
        if stdin == subprocess.PIPE:
            self.stdin = _DockerSocketWriter(self)
        elif (
            stdin not in (None, subprocess.DEVNULL)
            and stdin != subprocess.PIPE
            and hasattr(stdin, "read")
        ):
            self._stdin_source = stdin
        self._stdout_sink = _resolve_output_sink(stdout)
        if not self._merge_stderr_to_stdout:
            self._stderr_sink = _resolve_output_sink(stderr)
        if self._demux_capture and stdout == subprocess.PIPE:
            self.stdout = io.BufferedReader(_MemoryBufferReader(self._stdout_buf))
        if self._demux_capture and stderr == subprocess.PIPE:
            self.stderr = io.BufferedReader(_MemoryBufferReader(self._stderr_buf))

    def communicate(
        self, input: str | bytes | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        if self._forward_stdio and self._interactive_tty:
            self._forward_current_stdio()
            return "", ""

        deadline = None if timeout is None else time.monotonic() + timeout
        if self._forward_stdio and not self._interactive_tty:
            stdin_data: str | bytes | None = None
            if sys.stdin and not sys.stdin.isatty():
                if hasattr(sys.stdin, "buffer"):
                    stdin_data = sys.stdin.buffer.read()
                else:
                    stdin_data = sys.stdin.read()
            stdout, stderr = self._communicate_capture(
                input=stdin_data if input is None else input,
                deadline=deadline,
                timeout=timeout,
            )
            if stdout:
                if hasattr(sys.stdout, "buffer"):
                    sys.stdout.buffer.write(stdout.encode())
                    sys.stdout.buffer.flush()
                else:
                    sys.stdout.write(stdout)
                    sys.stdout.flush()
            if stderr:
                if hasattr(sys.stderr, "buffer"):
                    sys.stderr.buffer.write(stderr.encode())
                    sys.stderr.buffer.flush()
                else:
                    sys.stderr.write(stderr)
                    sys.stderr.flush()
            return "", ""
        return self._communicate_capture(
            input=input, deadline=deadline, timeout=timeout
        )

    def _communicate_capture(
        self,
        *,
        input: str | bytes | None,
        deadline: float | None,
        timeout: float | None,
    ) -> tuple[str, str]:
        if (
            input is None
            and self._stdin_source is not None
            and hasattr(self._stdin_source, "read")
        ):
            input = self._stdin_source.read()  # type: ignore[attr-defined]
        if input is not None and self._socket is not None:
            if isinstance(input, str):
                payload = input.encode()
            else:
                payload = input
            if payload:
                _socket_send(self._socket, payload)
            self._close_stdin()
        if self._demux_capture:
            stdout_chunks = list(self._initial_stdout) if self._capture_stdout else []
            stderr_chunks = list(self._initial_stderr) if self._capture_stderr else []
            socket_open = True
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args or [], timeout or 0)
                if socket_open and _poll_mux_socket(self._socket, deadline):
                    frame = _read_mux_frame(self._socket, deadline)
                    if frame is None:
                        socket_open = False
                    else:
                        self._dispatch_mux_frame(*frame, stdout_chunks, stderr_chunks)
                exit_code = self.poll()
                if exit_code is not None and not socket_open:
                    return (
                        b"".join(stdout_chunks).decode(errors="replace"),
                        b"".join(stderr_chunks).decode(errors="replace"),
                    )
            # unreachable

        if self._stream is None:
            raise RuntimeError("docker exec stream is not available for capture")
        chunks: list[bytes] = []
        for chunk in self._stream:
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.args or [], timeout or 0)
            if not chunk:
                continue
            chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))
        self._stdout_buf.write(b"".join(chunks))
        return b"".join(chunks).decode(errors="replace"), ""

    def _dispatch_mux_frame(
        self,
        stream_type: int,
        payload: bytes,
        stdout_chunks: list[bytes],
        stderr_chunks: list[bytes],
    ) -> None:
        if stream_type == 1:
            _write_output_sink(self._stdout_sink, payload)
            if self._capture_stdout:
                stdout_chunks.append(payload)
                self._stdout_buf.write(payload)
        elif stream_type == 2:
            if self._merge_stderr_to_stdout:
                _write_output_sink(self._stdout_sink, payload)
                if self._capture_stdout:
                    stdout_chunks.append(payload)
                    self._stdout_buf.write(payload)
            else:
                _write_output_sink(self._stderr_sink, payload)
                if self._capture_stderr:
                    stderr_chunks.append(payload)
                    self._stderr_buf.write(payload)

    def _forward_current_stdio(self) -> None:
        sock = self._socket
        tty_stdout = self._tty_stdout if self._tty_stdout is not None else sys.stdout
        stdout = tty_stdout.buffer if hasattr(tty_stdout, "buffer") else None
        stdin_fd = self._tty_stdin.fileno() if self._tty_stdin is not None else None
        original_tty = None
        old_winch_handler = None

        def write_stdout(chunk: bytes) -> None:
            if stdout is not None:
                stdout.write(chunk)
                stdout.flush()
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
                # Resize failures should not break interactive sessions.
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
            sig_name = sig.removeprefix("SIG")
        self._signal_process(sig_name)

    def terminate(self) -> None:
        self.send_signal("TERM")

    def kill(self) -> None:
        self.send_signal("KILL")

    def _signal_process(self, sig: str) -> None:
        try:
            payload = self._api.exec_create(
                container=self._container_id,
                cmd=["kill", f"-{sig}", str(self.pid)],
                tty=False,
                stdin=False,
            )
            self._api.exec_start(payload["Id"], stream=False, demux=False, tty=False)
        except Exception:
            # Timeout should still surface even if kill best-effort fails.
            return

    def _close_stdin(self) -> None:
        if self._stdin_closed:
            return
        self._stdin_closed = True
        try:
            if hasattr(self._socket, "shutdown"):
                self._socket.shutdown(socket.SHUT_WR)  # type: ignore[attr-defined]
        except Exception:
            return

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
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:
                pass
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @classmethod
    def from_tty_exec(
        cls,
        *,
        api: APIClient,
        exec_id: str,
        container_id: str,
        socket: object,
        args: Command | None = None,
        stdin: StdioValue = None,
        stdout: StdioValue = None,
        stderr: StdioValue = None,
    ) -> Self:
        stream_iter = _socket_chunk_iter(socket)
        pid_line, buffered = _read_pid_and_buffer(stream_iter)
        pid = _parse_pid_line(pid_line)
        return cls(
            api=api,
            exec_id=exec_id,
            container_id=container_id,
            pid=pid,
            socket=socket,
            stream=chain(buffered, stream_iter),
            args=args,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )

    @classmethod
    def from_mux_exec(
        cls,
        *,
        api: APIClient,
        exec_id: str,
        container_id: str,
        socket: object,
        args: Command | None = None,
        stdin: StdioValue = None,
        stdout: StdioValue = None,
        stderr: StdioValue = None,
    ) -> Self:
        pid_line, initial_stdout, initial_stderr = _read_pid_and_buffer_mux(
            socket, timeout=5.0
        )
        pid = _parse_pid_line(pid_line)
        return cls(
            api=api,
            exec_id=exec_id,
            container_id=container_id,
            pid=pid,
            socket=socket,
            demux_capture=True,
            initial_stdout=initial_stdout,
            initial_stderr=initial_stderr,
            args=args,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )


class DockerSubprocess:
    """A lightweight subprocess-like facade on top of Docker exec."""

    def __init__(self, *, api: APIClient, container_id: str) -> None:
        self._api = api
        self._container_id = container_id

    def Popen(
        self,
        command: Command,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        stdin: StdioValue = None,
        stdout: StdioValue = None,
        stderr: StdioValue = None,
    ) -> DockerPopen:
        cmd_str = normalize_shell_command(command)
        wrapper = self._build_shell_wrapper(cmd_str)
        use_tty_protocol = bool(
            _resolve_tty_stream(stdin, fallback=sys.stdin)
            and _resolve_tty_stream(stdout, fallback=sys.stdout)
        )
        payload = self._api.exec_create(
            container=self._container_id,
            cmd=["sh", "-lc", wrapper],
            workdir=cwd,
            environment=env or None,
            tty=use_tty_protocol,
            stdin=(stdin != subprocess.DEVNULL),
        )
        exec_id = payload["Id"]
        raw_sock = self._api.exec_start(exec_id, socket=True, tty=use_tty_protocol)
        io_socket = getattr(raw_sock, "_sock", raw_sock)
        factory = (
            DockerPopen.from_tty_exec if use_tty_protocol else DockerPopen.from_mux_exec
        )
        return factory(
            api=self._api,
            exec_id=exec_id,
            container_id=self._container_id,
            socket=io_socket,
            args=command,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
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
        with self.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as proc:
            try:
                stdout, stderr = proc.communicate(input=input, timeout=timeout)
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._cleanup_timed_out_process(proc)
                raise
        completed = subprocess.CompletedProcess(
            args=command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                command,
                output=stdout,
                stderr=stderr,
            )
        return completed

    def run_fg(
        self,
        command: Command,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        with self.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=None,
            stdout=None,
            stderr=None,
        ) as proc:
            try:
                proc.communicate()
                exit_code = proc.wait()
            except BaseException:
                self._cleanup_timed_out_process(proc)
                raise
        if exit_code != 0:
            raise RuntimeError(f"docker exec failed with exit code {exit_code}")

    @staticmethod
    def _build_shell_wrapper(command: str) -> str:
        return f"echo $$; exec sh -lc {shlex.quote(command)}"

    @staticmethod
    def _cleanup_timed_out_process(proc: DockerPopen) -> None:
        proc.terminate()
        time.sleep(0.05)
        if proc.poll() is None:
            proc.kill()


class _MemoryBufferReader(io.RawIOBase):
    def __init__(self, buf: io.BytesIO) -> None:
        self._buf = buf

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = self._buf.read(len(b))
        n = len(data)
        b[:n] = data
        return n


class _DockerSocketWriter(io.BufferedWriter):
    def __init__(self, proc: DockerPopen) -> None:
        super().__init__(io.BytesIO())
        self._proc = proc

    def writable(self) -> bool:
        return True

    def write(self, b: bytes) -> int:
        if not b:
            return 0
        _socket_send(self._proc._socket, b)  # noqa: SLF001
        return len(b)

    def close(self) -> None:
        self._proc._close_stdin()  # noqa: SLF001
        super().close()


def _resolve_output_sink(spec: StdioValue) -> int | IO[Any] | None:
    if spec in (None, subprocess.PIPE, subprocess.DEVNULL, subprocess.STDOUT):
        return None
    return spec


def _resolve_tty_stream(
    spec: StdioValue, *, fallback: IO[Any] | None
) -> IO[Any] | None:
    stream = fallback if spec is None else spec
    if stream is None or isinstance(stream, int):
        return None
    if stream in (subprocess.PIPE, subprocess.DEVNULL, subprocess.STDOUT):
        return None
    if hasattr(stream, "isatty") and stream.isatty():
        return stream
    return None


def _write_output_sink(sink: int | IO[Any] | None, payload: bytes) -> None:
    if sink is None or not payload:
        return
    if isinstance(sink, int):
        os.write(sink, payload)
        return
    if hasattr(sink, "buffer"):
        sink.buffer.write(payload)  # type: ignore[attr-defined]
        sink.buffer.flush()  # type: ignore[attr-defined]
        return
    if hasattr(sink, "write"):
        try:
            sink.write(payload)  # type: ignore[attr-defined]
        except TypeError:
            sink.write(payload.decode(errors="replace"))  # type: ignore[attr-defined]
        if hasattr(sink, "flush"):
            sink.flush()  # type: ignore[attr-defined]


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


def _poll_mux_socket(sock: object, deadline: float | None) -> bool:
    """Returns True if the socket has data ready, or if it doesn't support select."""
    if not hasattr(sock, "fileno"):
        return True
    wait_s = _remaining_timeout(deadline, cap=0.05)
    ready, _, _ = select.select([sock], [], [], wait_s)
    return sock in ready


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


def _build_put_archive_payload(src_host: str, dest_runtime: str) -> tuple[str, bytes]:
    src_path = Path(src_host)
    if not src_path.exists():
        raise RuntimeError(f"Source path does not exist: {src_host}")

    dest_path = Path(dest_runtime)
    tar_buffer = io.BytesIO()

    # Docker Engine: put_archive `path` must already exist in the container; the
    # archive root member is extracted as a child under that path. Use the parent
    # of dest + basename(dest) so e.g. copy_to(..., "/skills/docx") works after
    # `rm -rf /skills/docx` as long as `/skills` exists (same as file copy).
    put_path = str(dest_path.parent) if str(dest_path.parent) else "."
    arcname = dest_path.name

    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        tar.add(src_path, arcname=arcname)
    tar_buffer.seek(0)
    return put_path, tar_buffer.getvalue()


def _extract_archive_to_host(
    archive_stream: Iterable[bytes],
    *,
    src_runtime: str,
    dest_host: str,
    stat: dict[str, object] | None,
) -> None:
    archive_bytes = b"".join(
        chunk if isinstance(chunk, bytes) else bytes(chunk)
        for chunk in archive_stream
        if chunk
    )

    src_name = (
        Path(str(stat.get("name"))) if stat and stat.get("name") else Path(src_runtime)
    )
    src_is_dir = bool(stat and stat.get("mode") and int(stat["mode"]) & 0o040000)
    dest_path = Path(dest_host)

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tar:
        if src_is_dir:
            dest_path.mkdir(parents=True, exist_ok=True)
            tar.extractall(path=dest_path)
            return

        members = tar.getmembers()
        if not members:
            raise RuntimeError("docker get_archive returned empty archive")

        file_member = next(
            (member for member in members if member.isfile()), members[0]
        )
        extracted = tar.extractfile(file_member)
        if extracted is None:
            raise RuntimeError("docker get_archive returned unreadable file")

        if dest_path.exists() and dest_path.is_dir():
            output_path = dest_path / src_name.name
        else:
            output_path = dest_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(extracted.read())


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
        timeout: float | None = None,
    ) -> ExecResult:
        completed = self._docker_subprocess().run(
            command=command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            check=False,
        )
        return ExecResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def exec_bg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        self._docker_subprocess().Popen(
            command=command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def exec_fg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        self._docker_subprocess().run_fg(command, cwd=cwd, env=env)

    def copy_to(self, src_host: str, dest_runtime: str) -> None:
        container = self._client().containers.get(self._require_container_id())
        put_path, archive_payload = _build_put_archive_payload(src_host, dest_runtime)
        ok = container.put_archive(put_path, archive_payload)
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
        )
