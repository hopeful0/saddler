from __future__ import annotations

import os
import shlex
import signal
import sys
from dataclasses import dataclass
from typing import IO, Protocol, Self

import select

from pydantic import JsonValue

from .model import RuntimeSpec

from ..shared.registry import Registry

# str is passed as-is; list[str] is joined via shlex.join — both run under shell semantics (sh -lc).
Command = str | list[str]


def normalize_shell_command(command: Command) -> str:
    if isinstance(command, str):
        if not command.strip():
            raise ValueError("command must not be empty")
        return command

    if not command:
        raise ValueError("command must not be empty")

    if not all(isinstance(item, str) for item in command):
        raise TypeError("command list items must be str")

    return shlex.join(command)


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


class ProcessHandle(Protocol):
    stdin: IO[bytes] | None
    stdout: IO[bytes] | None
    stderr: IO[bytes] | None
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int: ...
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def resize(self, rows: int, cols: int) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class RuntimeBackend(Protocol):
    @classmethod
    def create(cls, spec: RuntimeSpec) -> Self:
        """Construct a backend instance from a RuntimeSpec."""

    def start(self) -> None:
        """Start the runtime. If already stopped (but not removed), restart it."""

    def stop(self) -> None:
        """Gracefully stop the runtime without destroying it; can be restarted via start()."""

    def remove(self) -> None:
        """Destroy the runtime and release all associated resources."""

    def is_running(self) -> bool:
        """Return True if the runtime is active and ready to accept commands."""

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
        """Run a shell command and return process handle.

        command accepts str | list[str]. list[str] is normalized via shlex.join
        into a shell command string before execution, and execution follows shell
        semantics.
        """

    def copy_to(self, src_host: str, dest_runtime: str) -> None:
        """Copy a file or directory from the host filesystem into the runtime."""

    def copy_from(self, src_runtime: str, dest_host: str) -> None:
        """Copy a file or directory from the runtime to the host filesystem."""

    def dump_state(self) -> JsonValue | None:
        """Serialize backend state for persistence; return None if the backend is stateless."""

    @classmethod
    def load_state(cls, spec: RuntimeSpec, state: JsonValue | None) -> Self:
        """Reconstruct a backend instance from a previously dumped state blob."""


RUNTIME_BACKEND_REGISTRY = Registry[type[RuntimeBackend]](
    group="saddler.runtime.backend"
)


def register_runtime_backend(runtime_type: str):
    """Decorator to register a RuntimeBackend class into the registry."""

    def wrapper(cls: type[RuntimeBackend]) -> type[RuntimeBackend]:
        RUNTIME_BACKEND_REGISTRY.register(runtime_type, cls)
        return cls

    return wrapper


def get_runtime_backend_cls(runtime_type: str) -> type[RuntimeBackend]:
    return RUNTIME_BACKEND_REGISTRY.get(runtime_type)


def exec_capture(
    backend: RuntimeBackend,
    command: Command,
    cwd: str,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> ExecResult:
    with backend.exec(
        command,
        cwd,
        env=env,
        stdin=False,
        stdout=True,
        stderr=True,
        tty=False,
        detach=False,
        timeout=timeout,
    ) as proc:
        assert proc is not None
        out_fd = proc.stdout
        err_fd = proc.stderr
        if out_fd is None or err_fd is None:
            raise RuntimeError("backend.exec(stdout=True, stderr=True) must expose IO")
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        streams = {out_fd: out_chunks, err_fd: err_chunks}
        while streams:
            if timeout is not None and proc.poll() is None:
                # wait() handles timeout precisely; this loop just drains IO.
                pass
            readable, _, _ = select.select(list(streams), [], [], 0.05)
            for stream in readable:
                chunk = stream.read(4096)
                if chunk:
                    streams[stream].append(chunk)
                else:
                    streams.pop(stream, None)
        code = proc.wait(timeout=timeout)
        return ExecResult(
            exit_code=code,
            stdout=b"".join(out_chunks).decode(errors="replace"),
            stderr=b"".join(err_chunks).decode(errors="replace"),
        )


def exec_fg(
    backend: RuntimeBackend,
    command: Command,
    cwd: str,
    env: dict[str, str] | None = None,
) -> None:
    interactive = bool(sys.stdin and sys.stdin.isatty())

    def _write_bytes(stream: object, data: bytes) -> None:
        if hasattr(stream, "fileno"):
            try:
                os.write(stream.fileno(), data)  # type: ignore[arg-type]
                return
            except Exception:
                pass
        if hasattr(stream, "buffer"):
            stream.buffer.write(data)  # type: ignore[attr-defined]
            stream.buffer.flush()  # type: ignore[attr-defined]
            return
        stream.write(data.decode(errors="replace"))  # type: ignore[attr-defined]
        if hasattr(stream, "flush"):
            stream.flush()  # type: ignore[attr-defined]

    with backend.exec(
        command,
        cwd,
        env=env,
        stdin=True,
        stdout=True,
        stderr=not interactive,
        tty=interactive,
        detach=False,
    ) as proc:
        assert proc is not None
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("interactive exec requires stdin/stdout")
        if interactive:
            import termios
            import tty

            stdin_fd = sys.stdin.fileno()
            stdout_fd = sys.stdout.fileno()
            old = termios.tcgetattr(stdin_fd)
            old_winch = signal.getsignal(signal.SIGWINCH)

            def _resize(*_: object) -> None:
                cols, rows = shutil.get_terminal_size(fallback=(80, 24))
                proc.resize(rows, cols)

            import shutil

            try:
                tty.setraw(stdin_fd)
                signal.signal(signal.SIGWINCH, _resize)
                _resize()
                while True:
                    watch = [stdin_fd, proc.stdout]
                    ready, _, _ = select.select(watch, [], [], 0.05)
                    if stdin_fd in ready:
                        data = os.read(stdin_fd, 4096)
                        if data:
                            proc.stdin.write(data)
                            proc.stdin.flush()
                    if proc.stdout in ready:
                        chunk = os.read(proc.stdout.fileno(), 4096)
                        if chunk:
                            os.write(stdout_fd, chunk)
                    if proc.poll() is not None:
                        break
            finally:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)
                signal.signal(signal.SIGWINCH, old_winch)
        else:
            out = proc.stdout
            err = proc.stderr
            read_set: list[object] = [out]
            if err is not None:
                read_set.append(err)
            stdin_obj = proc.stdin
            stdin_fd = None
            if sys.stdin and not sys.stdin.isatty():
                try:
                    stdin_fd = sys.stdin.fileno()
                except Exception:
                    stdin_fd = None
            while read_set or stdin_fd is not None:
                watch = list(read_set)
                if stdin_fd is not None:
                    watch.append(stdin_fd)
                ready, _, _ = select.select(watch, [], [], 0.05)
                if stdin_fd is not None and stdin_fd in ready and stdin_obj is not None:
                    data = os.read(stdin_fd, 4096)
                    if data:
                        stdin_obj.write(data)
                        stdin_obj.flush()
                    else:
                        stdin_fd = None
                        try:
                            stdin_obj.close()
                        except Exception:
                            pass
                for stream in list(read_set):
                    if stream in ready:
                        chunk = stream.read(4096)  # type: ignore[attr-defined]
                        if chunk:
                            if stream is out:
                                _write_bytes(sys.stdout, chunk)
                            else:
                                _write_bytes(sys.stderr, chunk)
                        else:
                            read_set.remove(stream)
            if proc.poll() is None:
                proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"exec_fg failed with exit code {proc.returncode}")


def exec_bg(
    backend: RuntimeBackend,
    command: Command,
    cwd: str,
    env: dict[str, str] | None = None,
) -> None:
    backend.exec(
        command,
        cwd,
        env=env,
        stdin=False,
        stdout=False,
        stderr=False,
        tty=False,
        detach=True,
    )
