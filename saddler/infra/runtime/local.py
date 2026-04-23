from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Self

from pydantic import JsonValue

from ...runtime.backend import ExecResult, register_runtime_backend
from ...runtime.model import RuntimeSpec


@register_runtime_backend("local")
class LocalRuntimeBackend:
    def __init__(self, spec: RuntimeSpec) -> None:
        self.spec = spec

    @classmethod
    def create(cls, spec: RuntimeSpec) -> Self:
        return cls(spec=spec)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def remove(self) -> None:
        pass

    def is_running(self) -> bool:
        return True

    def exec(
        self,
        command: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        merged_env = {**os.environ, **self.spec.env, **(env or {})}
        proc = subprocess.run(
            command,
            cwd=cwd,
            env=merged_env,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        return ExecResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def exec_bg(
        self,
        command: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        merged_env = {**os.environ, **self.spec.env, **(env or {})}
        subprocess.Popen(
            command,
            cwd=cwd,
            env=merged_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def exec_fg(
        self,
        command: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        merged_env = {**os.environ, **self.spec.env, **(env or {})}
        proc = subprocess.run(command, cwd=cwd, env=merged_env, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"local exec failed with exit code {proc.returncode}")

    def copy_to(self, src_host: str, dest_runtime: str) -> None:
        src = Path(src_host)
        dest = Path(dest_runtime)
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    def copy_from(self, src_runtime: str, dest_host: str) -> None:
        self.copy_to(src_runtime, dest_host)

    def dump_state(self) -> JsonValue | None:
        return None

    @classmethod
    def load_state(cls, spec: RuntimeSpec, _state: JsonValue | None) -> Self:
        return cls(spec=spec)
