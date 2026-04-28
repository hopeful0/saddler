from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Protocol, Self

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
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a shell command and capture stdout/stderr; never raises on non-zero exit.

        command accepts str | list[str]. list[str] is normalized via shlex.join
        into a shell command string before execution, and execution follows shell
        semantics.
        """

    def exec_bg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        """Launch a shell command detached (fire-and-forget); output is discarded.

        command accepts str | list[str]. list[str] is normalized via shlex.join
        into a shell command string before execution, and execution follows shell
        semantics.
        """
        ...

    def exec_fg(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        """Run a shell command attached to terminal IO; raises on non-zero exit.

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
