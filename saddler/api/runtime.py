from __future__ import annotations

from dataclasses import dataclass, field
import logging

from ..app.runtime import RuntimeUseCase
from ..app.storage import StorageUseCase
from ..runtime.backend import Command, ExecResult, get_runtime_backend_cls
from ..runtime.model import (
    Runtime,
    RuntimeHostBindMount,
    RuntimeMountMode,
    RuntimeSpec,
    RuntimeStorageMount,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MountSpec:
    """Parsed CLI mount entry before storage IDs are resolved."""

    type: str  # "storage" | "bind"
    destination: str
    storage_ref: str | None = None  # type="storage"
    source: str | None = None  # type="bind"
    mode: str = "rw"


@dataclass(frozen=True)
class RuntimeCreateRequest:
    backend_type: str
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[MountSpec] = field(default_factory=list)
    backend_spec: dict | None = None
    name: str | None = None
    metadata: dict[str, str] | None = None


class RuntimeApiService:
    def __init__(
        self,
        use_case: RuntimeUseCase,
        storage_uc: StorageUseCase,
    ) -> None:
        self._uc = use_case
        self._storage_uc = storage_uc

    def create(self, req: RuntimeCreateRequest) -> Runtime:
        resolved_mounts = [self._resolve_mount(m) for m in req.mounts]
        spec = RuntimeSpec(
            backend_type=req.backend_type,
            env=req.env,
            mounts=resolved_mounts,
            backend_spec=req.backend_spec,
        )
        return self._uc.create_runtime(spec, name=req.name, metadata=req.metadata)

    def start(self, ref: str) -> Runtime:
        return self._uc.start_runtime(ref)

    def stop(self, ref: str) -> Runtime:
        return self._uc.stop_runtime(ref)

    def remove(self, ref: str, *, force: bool = False) -> None:
        self._uc.remove_runtime(ref, force=force)

    def list(self) -> list[Runtime]:
        return self._uc.list_runtimes()

    def list_with_status(self) -> list[tuple[Runtime, str]]:
        records = self._uc.list_runtimes()
        return [(runtime, self._runtime_status(runtime)) for runtime in records]

    def inspect(self, ref: str) -> Runtime:
        return self._uc.get_runtime(ref)

    def exec(
        self,
        ref: str,
        command: Command,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        runtime = self._uc.get_runtime(ref)
        backend = get_runtime_backend_cls(runtime.spec.backend_type).load_state(
            runtime.spec, runtime.backend_state
        )
        return backend.exec(command, cwd=cwd, env=env, timeout=timeout)

    def exec_fg(
        self,
        ref: str,
        command: Command,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        runtime = self._uc.get_runtime(ref)
        backend = get_runtime_backend_cls(runtime.spec.backend_type).load_state(
            runtime.spec, runtime.backend_state
        )
        backend.exec_fg(command, cwd=cwd, env=env)

    def _resolve_mount(self, m: MountSpec):
        mode = RuntimeMountMode(m.mode.lower())
        if m.type == "storage":
            storage = self._storage_uc.get_storage(m.storage_ref)  # type: ignore[arg-type]
            return RuntimeStorageMount(
                storage_id=storage.id,
                destination=m.destination,
                mode=mode,
            )
        if m.type == "bind":
            return RuntimeHostBindMount(
                source=m.source,  # type: ignore[arg-type]
                destination=m.destination,
                mode=mode,
            )
        raise ValueError(f"Unknown mount type: {m.type!r}")

    def _runtime_status(self, runtime: Runtime) -> str:
        try:
            backend = get_runtime_backend_cls(runtime.spec.backend_type).load_state(
                runtime.spec, runtime.backend_state
            )
            return "running" if backend.is_running() else "not running"
        except Exception:
            log.debug(
                "Failed to resolve runtime status for %s (%s)",
                runtime.name or "-",
                runtime.id,
                exc_info=True,
            )
            return "not running"
