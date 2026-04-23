from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Self

from pydantic import JsonValue

from .model import RuntimeSpec

from ..shared.registry import Registry


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
        command: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a command and capture its stdout/stderr; never raises on non-zero exit."""

    def exec_bg(
        self,
        command: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        """Launch a command detached (fire-and-forget); output is discarded."""
        ...

    def exec_fg(
        self,
        command: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        """Run a command with stdin/stdout/stderr attached to the terminal; raises on non-zero exit."""

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
