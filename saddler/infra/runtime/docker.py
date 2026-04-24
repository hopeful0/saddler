from __future__ import annotations

import subprocess
import uuid
from typing import Self

import docker
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
        cid = self._require_container_id()
        cmd_str = normalize_shell_command(command)
        args = ["exec", "-w", cwd]
        for key, value in (env or {}).items():
            args.extend(["-e", f"{key}={value}"])
        args.extend([cid, "sh", "-lc", cmd_str])
        proc = self._run_subprocess(["docker", *args], timeout=timeout, check=False)
        return ExecResult(
            exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
        )

    def exec_bg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        cid = self._require_container_id()
        cmd_str = normalize_shell_command(command)
        args = ["exec", "-d", "-w", cwd]
        for key, value in (env or {}).items():
            args.extend(["-e", f"{key}={value}"])
        args.extend([cid, "sh", "-lc", cmd_str])
        self._run_docker(args)

    def exec_fg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        import sys

        cid = self._require_container_id()
        cmd_str = normalize_shell_command(command)
        args = ["exec", "-i"]
        if sys.stdin.isatty():
            args.append("-t")
        args.extend(["-w", cwd])
        for key, value in (env or {}).items():
            args.extend(["-e", f"{key}={value}"])
        args.extend([cid, "sh", "-lc", cmd_str])
        proc = subprocess.run(["docker", *args], check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"docker exec failed with exit code {proc.returncode}")

    def copy_to(self, src_host: str, dest_runtime: str) -> None:
        cid = self._require_container_id()
        self._run_docker(["cp", src_host, f"{cid}:{dest_runtime}"])
        if self.docker_spec.user:
            # docker cp preserves host file UID/GID; fix ownership so the
            # container's non-root user can actually read/write the files.
            self._run_docker(
                [
                    "exec",
                    "-u",
                    "0",
                    cid,
                    "chown",
                    "-R",
                    self.docker_spec.user,
                    dest_runtime,
                ]
            )

    def copy_from(self, src_runtime: str, dest_host: str) -> None:
        cid = self._require_container_id()
        self._run_docker(["cp", f"{cid}:{src_runtime}", dest_host])

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

    def _run_docker(self, args: list[str]) -> str:
        proc = self._run_subprocess(["docker", *args], check=True)
        return proc.stdout

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

    @staticmethod
    def _run_subprocess(
        cmd: list[str], *, timeout: float | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                proc.stderr.strip() or proc.stdout.strip() or "docker command failed"
            )
        return proc
