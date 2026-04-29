from __future__ import annotations

import os
import pty
import shutil
import struct
import subprocess
import termios
import fcntl
from pathlib import Path
from typing import IO, Self

from pydantic import JsonValue

from ...runtime.backend import (
    Command,
    ProcessHandle,
    normalize_shell_command,
    register_runtime_backend,
)
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
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
        *,
        stdin: bool = False,
        stdout: bool = False,
        stderr: bool = False,
        tty: bool = False,
        detach: bool = False,
    ) -> ProcessHandle | None:
        cmd_str = normalize_shell_command(command)
        merged_env = {**os.environ, **self.spec.env, **(env or {})}
        if detach:
            subprocess.Popen(
                ["sh", "-lc", cmd_str],
                cwd=cwd,
                env=merged_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return None
        if tty:
            return LocalPtyHandle(
                command=["sh", "-lc", cmd_str],
                cwd=cwd,
                env=merged_env,
            )
        return LocalPipeHandle(
            command=["sh", "-lc", cmd_str],
            cwd=cwd,
            env=merged_env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )

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


class LocalPipeHandle:
    def __init__(
        self,
        *,
        command: list[str],
        cwd: str,
        env: dict[str, str],
        stdin: bool,
        stdout: bool,
        stderr: bool,
    ) -> None:
        self._proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if stdin else None,
            stdout=subprocess.PIPE if stdout else None,
            stderr=subprocess.PIPE if stderr else None,
        )
        self.stdin = self._proc.stdin
        self.stdout = self._proc.stdout
        self.stderr = self._proc.stderr

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
        _ = (rows, cols)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        # Ensure we never block indefinitely in context-manager cleanup.
        # `subprocess.Popen.__exit__()` calls `wait()` without a timeout, which
        # can hang if the child ignores SIGTERM.
        if self.poll() is None:
            self.terminate()
            try:
                self._proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.kill()
                try:
                    # Last bounded wait; if this still doesn't exit, we still
                    # must not hang the caller.
                    self._proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass

        for f in (self.stdin, self.stdout, self.stderr):
            if f is None:
                continue
            try:
                if not f.closed:
                    f.close()
            except Exception:
                pass


class LocalPtyHandle:
    def __init__(self, *, command: list[str], cwd: str, env: dict[str, str]) -> None:
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        self._master: IO[bytes] = os.fdopen(master_fd, "r+b", buffering=0)
        self._proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        self.stdin = self._master
        self.stdout = self._master
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
        winsz = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsz)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        # Ensure we never block indefinitely in context-manager cleanup.
        if self.poll() is None:
            self.terminate()
            try:
                self._proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.kill()
                try:
                    self._proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass

        if not self._master.closed:
            try:
                self._master.close()
            except Exception:
                pass
