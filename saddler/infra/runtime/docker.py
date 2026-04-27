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
from typing import Self

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


class DockerPopen:
    def __init__(
        self,
        *,
        api: APIClient,
        exec_id: str,
        container_id: str,
        stream: Iterable[bytes] | None,
        pid: int,
        socket: object | None = None,
        demux_capture: bool = False,
        initial_stdout: list[bytes] | None = None,
        initial_stderr: list[bytes] | None = None,
        args: Command | None = None,
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

    def communicate(
        self, input: str | bytes | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        deadline = None if timeout is None else time.monotonic() + timeout
        if input is not None and self._socket is not None:
            if isinstance(input, str):
                payload = input.encode()
            else:
                payload = input
            if payload:
                _socket_send(self._socket, payload)
            self._close_stdin()
        if self._demux_capture:
            if self._socket is None:
                raise RuntimeError("docker exec socket is not available for capture")
            stdout_chunks = list(self._initial_stdout)
            stderr_chunks = list(self._initial_stderr)
            socket_open = True
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args or [], timeout or 0)
                if socket_open:
                    if hasattr(self._socket, "fileno"):
                        wait_s = _remaining_timeout(deadline, cap=0.05)
                        ready, _, _ = select.select([self._socket], [], [], wait_s)
                        if self._socket in ready:
                            frame = _read_mux_frame(self._socket, deadline)
                            if frame is None:
                                socket_open = False
                            else:
                                stream_type, payload = frame
                                if stream_type == 1:
                                    stdout_chunks.append(payload)
                                elif stream_type == 2:
                                    stderr_chunks.append(payload)
                    else:
                        frame = _read_mux_frame(self._socket, deadline)
                        if frame is None:
                            socket_open = False
                        else:
                            stream_type, payload = frame
                            if stream_type == 1:
                                stdout_chunks.append(payload)
                            elif stream_type == 2:
                                stderr_chunks.append(payload)
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
        return b"".join(chunks).decode(errors="replace"), ""

    def attach_to_stdio(self) -> None:
        if self._socket is None:
            raise RuntimeError("docker exec socket is not available for foreground IO")
        sock = self._socket
        stdout = sys.stdout.buffer if hasattr(sys.stdout, "buffer") else None
        stdin_fd = sys.stdin.fileno() if sys.stdin and sys.stdin.isatty() else None
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
        if self._socket is None:
            return
        try:
            if hasattr(self._socket, "shutdown"):
                self._socket.shutdown(socket.SHUT_WR)  # type: ignore[attr-defined]
        except Exception:
            return

    @staticmethod
    def build_shell_wrapper(command: str) -> str:
        # Keep shell semantics by executing the normalized command string through
        # an inner shell, while printing wrapper PID first for strict handshake.
        return f"echo $$; exec sh -lc {shlex.quote(command)}"

    @classmethod
    def spawn(
        cls,
        *,
        api: APIClient,
        container_id: str,
        command: Command,
        cwd: str,
        env: dict[str, str] | None,
        mode: str,
    ) -> Self:
        cmd_str = normalize_shell_command(command)
        wrapper = cls.build_shell_wrapper(cmd_str)
        payload = api.exec_create(
            container=container_id,
            cmd=["sh", "-lc", wrapper],
            workdir=cwd,
            environment=env or None,
            tty=(mode == "fg"),
            stdin=(mode in {"fg", "capture"}),
        )
        exec_id = payload["Id"]
        if mode == "fg":
            raw_sock = api.exec_start(exec_id, socket=True, tty=True)
            io_socket = getattr(raw_sock, "_sock", raw_sock)
            stream_iter = _socket_chunk_iter(io_socket)
            pid_line, buffered = _read_pid_and_buffer(stream_iter)
            pid = _parse_pid_line(pid_line)
            full_stream = chain(buffered, stream_iter)
            return cls(
                api=api,
                exec_id=exec_id,
                container_id=container_id,
                stream=full_stream,
                pid=pid,
                socket=io_socket,
                args=command,
            )
        if mode == "capture":
            raw_sock = api.exec_start(exec_id, socket=True, tty=False)
            io_socket = getattr(raw_sock, "_sock", raw_sock)
            pid_line, initial_stdout, initial_stderr = _read_pid_and_buffer_mux(
                io_socket, timeout=5.0
            )
            pid = _parse_pid_line(pid_line)
            return cls(
                api=api,
                exec_id=exec_id,
                container_id=container_id,
                stream=None,
                pid=pid,
                socket=io_socket,
                demux_capture=True,
                initial_stdout=initial_stdout,
                initial_stderr=initial_stderr,
                args=command,
            )
        raw_stream = api.exec_start(exec_id, stream=True, demux=False, tty=False)
        stream_iter = iter(raw_stream)
        io_socket = None
        pid_line, buffered = _read_pid_and_buffer(stream_iter)
        pid = _parse_pid_line(pid_line)
        full_stream = chain(buffered, stream_iter)
        return cls(
            api=api,
            exec_id=exec_id,
            container_id=container_id,
            stream=full_stream,
            pid=pid,
            socket=io_socket,
            args=command,
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
        mode: str = "capture",
    ) -> DockerPopen:
        return DockerPopen.spawn(
            api=self._api,
            container_id=self._container_id,
            command=command,
            cwd=cwd,
            env=env,
            mode=mode,
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
        proc = self.Popen(command, cwd=cwd, env=env, mode="capture")
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

    @staticmethod
    def _cleanup_timed_out_process(proc: DockerPopen) -> None:
        proc.terminate()
        time.sleep(0.05)
        if proc.poll() is None:
            proc.kill()


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
        self._spawn_docker_popen(command=command, cwd=cwd, env=env, mode="bg")

    def exec_fg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        proc = self._spawn_docker_popen(command=command, cwd=cwd, env=env, mode="fg")
        proc.attach_to_stdio()
        exit_code = proc.wait()
        if exit_code != 0:
            raise RuntimeError(f"docker exec failed with exit code {exit_code}")

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

    def _spawn_docker_popen(
        self,
        *,
        command: Command,
        cwd: str,
        env: dict[str, str] | None,
        mode: str,
    ) -> DockerPopen:
        return self._docker_subprocess().Popen(
            command=command, cwd=cwd, env=env, mode=mode
        )

    def _docker_subprocess(self) -> DockerSubprocess:
        return DockerSubprocess(
            api=self._client().api,
            container_id=self._require_container_id(),
        )
