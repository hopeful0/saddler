from __future__ import annotations

import io
import os
from pathlib import Path, PurePosixPath
import select
import shutil
import struct
import subprocess
import tarfile
import sys
import tempfile
import time
import uuid
from typing import Self

import docker
from docker.errors import DockerException, NotFound
from docker.utils import socket as docker_socket
from pydantic import BaseModel, Field, JsonValue

from ...runtime.backend import (
    Command,
    ExecResult,
    normalize_shell_command,
    register_runtime_backend,
)
from ...runtime.model import RuntimeHostBindMount, RuntimeMountType, RuntimeSpec


_MAX_DOCKER_FRAME = 64 * 1024 * 1024
_DEFAULT_PID_HANDSHAKE_TIMEOUT_SECONDS = 5.0


def _escape_for_double_quoted_sh(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )


def _build_pid_handshake_command(normalized_command: str) -> str:
    escaped_command = _escape_for_double_quoted_sh(normalized_command)
    return f'echo $$; exec sh -lc "{escaped_command}"'


def _env_list(env: dict[str, str] | None) -> list[str] | None:
    if not env:
        return None
    return [f"{key}={value}" for key, value in env.items()]


class DockerPopen:
    def __init__(
        self,
        *,
        api: docker.APIClient,
        exec_id: str,
        sock: object,
        command: str,
        timeout: float | None,
        tty: bool,
    ) -> None:
        self._api = api
        self._exec_id = exec_id
        self._sock = sock
        self._command = command
        self._timeout = timeout
        self._command_deadline = (
            None if timeout is None else (time.monotonic() + timeout)
        )
        self._raw_buffer = b""
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._stream_closed = False
        self._sock_closed = False
        self._tty = tty
        self.pid = 0

    @classmethod
    def spawn(
        cls,
        *,
        api: docker.APIClient,
        container_id: str,
        wrapped_command: str,
        cwd: str,
        env: dict[str, str] | None,
        timeout: float | None,
        handshake_timeout: float | None,
        tty: bool,
    ) -> DockerPopen:
        exec_id = api.exec_create(
            container_id,
            ["sh", "-lc", wrapped_command],
            stdin=tty,
            stdout=True,
            stderr=True,
            tty=tty,
            workdir=cwd,
            environment=_env_list(env),
        )["Id"]
        sock = api.exec_start(exec_id, tty=tty, socket=True, stream=True)
        popen = cls(
            api=api,
            exec_id=exec_id,
            sock=sock,
            command=wrapped_command,
            timeout=timeout,
            tty=tty,
        )
        popen._perform_pid_handshake(handshake_timeout=handshake_timeout)
        return popen

    def _read_deadline(
        self, *, handshake_deadline: float | None = None
    ) -> float | None:
        if handshake_deadline is None:
            return self._command_deadline
        if self._command_deadline is None:
            return handshake_deadline
        return min(handshake_deadline, self._command_deadline)

    def _read_next_chunk(self, *, deadline: float | None) -> bytes:
        if deadline is None:
            ready, _, _ = select.select([self._sock.fileno()], [], [])
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    cmd=self._command, timeout=self._timeout
                )
            ready, _, _ = select.select([self._sock.fileno()], [], [], remaining)
        if not ready:
            raise subprocess.TimeoutExpired(cmd=self._command, timeout=self._timeout)
        chunk = docker_socket.read(self._sock, 65536)
        if isinstance(chunk, bytes):
            return chunk
        return b""

    def _consume_mux_frames(self) -> None:
        if self._tty:
            if self._raw_buffer:
                self._stdout_buffer.extend(self._raw_buffer)
                self._raw_buffer = b""
            return
        while len(self._raw_buffer) >= 8:
            stream_type, size = struct.unpack(">BxxxL", self._raw_buffer[:8])
            if size > _MAX_DOCKER_FRAME:
                raise RuntimeError(f"docker stream frame too large: {size} bytes")
            frame_end = 8 + size
            if len(self._raw_buffer) < frame_end:
                break
            payload = self._raw_buffer[8:frame_end]
            self._raw_buffer = self._raw_buffer[frame_end:]
            if stream_type == docker_socket.STDOUT:
                self._stdout_buffer.extend(payload)
            elif stream_type == docker_socket.STDERR:
                self._stderr_buffer.extend(payload)

    def _pump_once(self, *, deadline: float | None = None) -> None:
        chunk = self._read_next_chunk(deadline=deadline)
        if not chunk:
            self._stream_closed = True
            return
        self._raw_buffer += chunk
        self._consume_mux_frames()

    def _perform_pid_handshake(self, *, handshake_timeout: float | None) -> None:
        handshake_deadline = (
            None
            if handshake_timeout is None
            else (time.monotonic() + handshake_timeout)
        )
        deadline = self._read_deadline(handshake_deadline=handshake_deadline)
        while True:
            line_end = self._stdout_buffer.find(b"\n")
            if line_end >= 0:
                raw_pid = bytes(self._stdout_buffer[:line_end]).strip()
                del self._stdout_buffer[: line_end + 1]
                if not raw_pid:
                    self.close()
                    raise RuntimeError(
                        "docker exec pid handshake failed: missing pid line"
                    )
                try:
                    pid = int(raw_pid.decode("utf-8", errors="replace"))
                except ValueError as exc:
                    self.close()
                    raise RuntimeError(
                        f"docker exec pid handshake failed: non-integer pid {raw_pid!r}"
                    ) from exc
                if pid <= 0:
                    self.close()
                    raise RuntimeError(
                        f"docker exec pid handshake failed: invalid pid {pid}"
                    )
                self.pid = pid
                return
            if self._stream_closed:
                self.close()
                raise RuntimeError("docker exec pid handshake failed: missing pid line")
            try:
                self._pump_once(deadline=deadline)
            except subprocess.TimeoutExpired as exc:
                self.close()
                raise RuntimeError(
                    "docker exec pid handshake failed: timed out"
                ) from exc

    def _read_exit_code(self) -> int:
        inspected = self._api.exec_inspect(self._exec_id)
        exit_code = inspected.get("ExitCode")
        return exit_code if isinstance(exit_code, int) else -1

    def wait_capture(self) -> ExecResult:
        try:
            while not self._stream_closed:
                self._pump_once(deadline=self._command_deadline)
            return ExecResult(
                exit_code=self._read_exit_code(),
                stdout=self._stdout_buffer.decode(errors="replace"),
                stderr=self._stderr_buffer.decode(errors="replace"),
            )
        finally:
            self.close()

    def wait_foreground(self) -> int:
        try:
            self._flush_to_stdio()
            while not self._stream_closed:
                self._pump_once()
                self._flush_to_stdio()
            return self._read_exit_code()
        finally:
            self.close()

    def detach(self) -> None:
        self.close()

    def _flush_to_stdio(self) -> None:
        if self._stdout_buffer:
            sys.stdout.buffer.write(bytes(self._stdout_buffer))
            sys.stdout.buffer.flush()
            self._stdout_buffer.clear()
        if self._stderr_buffer:
            sys.stderr.buffer.write(bytes(self._stderr_buffer))
            sys.stderr.buffer.flush()
            self._stderr_buffer.clear()

    def close(self) -> None:
        if self._sock_closed:
            return
        self._sock_closed = True
        try:
            self._sock.close()  # type: ignore[union-attr]
        except OSError:
            pass


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
        self.state = state or DockerRuntimeState(
            container_name=docker_spec.container_name,
            image=docker_spec.image,
        )

    @classmethod
    def create(cls, spec: RuntimeSpec) -> Self:
        return cls(spec=spec, docker_spec=DockerRuntimeSpec.from_runtime_spec(spec))

    def start(self) -> None:
        client = self._docker_client()
        try:
            if self.state.container_id:
                client.containers.get(self.state.container_id).start()
                return

            container_name = (
                self.docker_spec.container_name or f"saddler-{uuid.uuid4().hex[:12]}"
            )
            run_kwargs: dict[str, object] = {
                "image": self.docker_spec.image,
                "command": self.docker_spec.command,
                "detach": True,
                "name": container_name,
            }
            if self.docker_spec.user:
                run_kwargs["user"] = self.docker_spec.user
            if self.spec.env:
                run_kwargs["environment"] = self.spec.env

            volumes = self._build_docker_volumes()
            if volumes:
                run_kwargs["volumes"] = volumes

            container = client.containers.run(**run_kwargs)
            self.state = DockerRuntimeState(
                container_id=container.id,
                container_name=container_name,
                image=self.docker_spec.image,
            )
        except DockerException as exc:
            raise RuntimeError(f"docker lifecycle operation failed: {exc}") from exc
        finally:
            client.close()

    def is_running(self) -> bool:
        if not self.state.container_id:
            return False
        client = self._docker_client()
        try:
            container = client.containers.get(self.state.container_id)
            container.reload()
            return container.status == "running"
        except NotFound:
            return False
        except DockerException as exc:
            raise RuntimeError(f"docker lifecycle operation failed: {exc}") from exc
        finally:
            client.close()

    def stop(self) -> None:
        if not self.state.container_id:
            return
        client = self._docker_client()
        try:
            client.containers.get(self.state.container_id).stop(timeout=10)
        except DockerException as exc:
            raise RuntimeError(f"docker lifecycle operation failed: {exc}") from exc
        finally:
            client.close()

    def remove(self) -> None:
        if not self.state.container_id:
            return
        client = self._docker_client()
        try:
            client.containers.get(self.state.container_id).remove(force=True)
        except DockerException as exc:
            raise RuntimeError(f"docker lifecycle operation failed: {exc}") from exc
        finally:
            client.close()
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
        popen, client = self._spawn_docker_popen(
            command=command,
            cwd=cwd,
            env=env,
            timeout=timeout,
            tty=False,
        )
        try:
            return popen.wait_capture()
        finally:
            client.close()

    def exec_bg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        popen, client = self._spawn_docker_popen(
            command=command,
            cwd=cwd,
            env=env,
            timeout=None,
            tty=False,
        )
        try:
            popen.detach()
        finally:
            client.close()

    def exec_fg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        popen, client = self._spawn_docker_popen(
            command=command,
            cwd=cwd,
            env=env,
            timeout=None,
            tty=sys.stdin.isatty(),
        )
        try:
            exit_code = popen.wait_foreground()
        finally:
            client.close()
        if exit_code != 0:
            raise RuntimeError(f"docker exec failed with exit code {exit_code}")

    def copy_to(self, src_host: str, dest_runtime: str) -> None:
        container_id = self._require_container_id()
        src = Path(src_host)
        if not src.exists():
            raise RuntimeError(f"host path does not exist: {src_host}")
        dest = PurePosixPath(dest_runtime)
        if not dest.is_absolute():
            raise RuntimeError(f"runtime path must be absolute: {dest_runtime}")
        arcname = str(dest).lstrip("/")
        if not arcname:
            raise RuntimeError("runtime path must not be root '/'")

        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            archive.add(src, arcname=arcname)
        payload.seek(0)

        client = self._docker_client()
        try:
            container = client.containers.get(container_id)
            uploaded = container.put_archive(path="/", data=payload.getvalue())
            if not uploaded:
                raise RuntimeError("docker put_archive returned False")
            if self.docker_spec.user:
                result = container.exec_run(
                    ["chown", "-R", self.docker_spec.user, str(dest)],
                    user="0",
                    workdir="/",
                )
                if result.exit_code != 0:
                    output = result.output
                    if isinstance(output, (bytes, bytearray)):
                        message = output.decode(errors="replace").strip()
                    else:
                        message = str(output).strip()
                    raise RuntimeError(message or "docker chown failed")
        except DockerException as exc:
            raise RuntimeError(f"docker copy operation failed: {exc}") from exc
        finally:
            client.close()

    def copy_from(self, src_runtime: str, dest_host: str) -> None:
        container_id = self._require_container_id()
        src = PurePosixPath(src_runtime)
        if not src.is_absolute():
            raise RuntimeError(f"runtime path must be absolute: {src_runtime}")

        client = self._docker_client()
        try:
            container = client.containers.get(container_id)
            stream, stat_info = container.get_archive(str(src))
            payload = io.BytesIO()
            for chunk in stream:
                payload.write(chunk)
            payload.seek(0)
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_root = Path(temp_dir)
                self._safe_extract_archive(payload, temp_root)
                extracted = self._resolve_extracted_root(
                    temp_root=temp_root,
                    src_runtime=src,
                    stat_info=stat_info,
                )
                self._copy_extracted_to_host(
                    extracted=extracted,
                    dest_host=Path(dest_host),
                )
        except DockerException as exc:
            raise RuntimeError(f"docker copy operation failed: {exc}") from exc
        finally:
            client.close()

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

    @staticmethod
    def _safe_extract_archive(payload: io.BytesIO, dest: Path) -> None:
        with tarfile.open(fileobj=payload, mode="r:*") as archive:
            members = archive.getmembers()
            dest_root = dest.resolve()
            for member in members:
                target = (dest / member.name).resolve()
                if target != dest_root and os.path.commonpath(
                    [str(dest_root), str(target)]
                ) != str(dest_root):
                    raise RuntimeError("docker archive contains unsafe member path")
            archive.extractall(path=dest, members=members, filter="data")

    @staticmethod
    def _resolve_extracted_root(
        *, temp_root: Path, src_runtime: PurePosixPath, stat_info: dict[str, object]
    ) -> Path:
        if isinstance(stat_info.get("name"), str):
            candidate = temp_root / stat_info["name"].lstrip("/")
            if candidate.exists():
                return candidate
        basename_candidate = temp_root / src_runtime.name
        if basename_candidate.exists():
            return basename_candidate
        children = list(temp_root.iterdir())
        if len(children) == 1:
            return children[0]
        raise RuntimeError("unable to determine extracted docker archive root")

    @staticmethod
    def _copy_extracted_to_host(*, extracted: Path, dest_host: Path) -> None:
        if extracted.is_dir():
            if dest_host.exists() and not dest_host.is_dir():
                raise RuntimeError(
                    f"cannot copy runtime directory into file destination: {dest_host}"
                )
            target = dest_host / extracted.name if dest_host.is_dir() else dest_host
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(extracted, target, dirs_exist_ok=True)
            return

        target = dest_host / extracted.name if dest_host.is_dir() else dest_host
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted, target)

    def _spawn_docker_popen(
        self,
        *,
        command: Command,
        cwd: str,
        env: dict[str, str] | None,
        timeout: float | None,
        tty: bool,
    ) -> tuple[DockerPopen, docker.DockerClient]:
        container_id = self._require_container_id()
        normalized_command = normalize_shell_command(command)
        wrapped_command = _build_pid_handshake_command(normalized_command)
        client = self._docker_client()
        try:
            popen = DockerPopen.spawn(
                api=client.api,
                container_id=container_id,
                wrapped_command=wrapped_command,
                cwd=cwd,
                env=env,
                timeout=timeout,
                handshake_timeout=_DEFAULT_PID_HANDSHAKE_TIMEOUT_SECONDS,
                tty=tty,
            )
        except DockerException as exc:
            client.close()
            raise RuntimeError(f"docker exec failed: {exc}") from exc
        except BaseException:
            client.close()
            raise
        return popen, client

    @staticmethod
    def _docker_client() -> docker.DockerClient:
        return docker.from_env()

    def _build_docker_volumes(self) -> dict[str, dict[str, str]]:
        volumes: dict[str, dict[str, str]] = {}
        for mount in self.spec.mounts:
            if mount.type != RuntimeMountType.BIND:
                raise RuntimeError(f"Unsupported docker mount type: {mount.type}")
            bind_mount = RuntimeHostBindMount.model_validate(mount)
            volumes[str(bind_mount.source)] = {
                "bind": str(bind_mount.destination),
                "mode": bind_mount.mode.value,
            }
        return volumes
