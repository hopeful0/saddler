from ..runtime.backend import get_runtime_backend_cls
from ..runtime.model import Runtime, RuntimeSpec, RuntimeStorageMount
from ..runtime.service import RuntimeService
from ..shared.repository import Repository
from ..storage.model import Storage
from .errors import ConflictError
from .resolver import NameResolver

_resolver = NameResolver[Runtime]("Runtime")


class RuntimeUseCase:
    def __init__(
        self,
        service: RuntimeService,
        repository: Repository[Runtime],
        storage_repo: Repository[Storage],
    ) -> None:
        self._service = service
        self._repo = repository
        self._storage_repo = storage_repo

    def create_runtime(
        self,
        spec: RuntimeSpec,
        *,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Runtime:
        runtime = self._service.create_runtime(spec, name=name, metadata=metadata)
        self._bind_storage_mounts(runtime, add=True)
        return runtime

    def start_runtime(self, ref: str) -> Runtime:
        runtime = _resolver.resolve(self._repo.list(), ref)
        backend = get_runtime_backend_cls(runtime.spec.backend_type).load_state(
            runtime.spec, runtime.backend_state
        )
        backend.start()
        self._repo.mutate(
            runtime.id,
            lambda r: r.model_copy(update={"backend_state": backend.dump_state()}),
        )
        return self._repo.get(runtime.id)  # type: ignore[return-value]

    def stop_runtime(self, ref: str) -> Runtime:
        runtime = _resolver.resolve(self._repo.list(), ref)
        backend = get_runtime_backend_cls(runtime.spec.backend_type).load_state(
            runtime.spec, runtime.backend_state
        )
        backend.stop()
        self._repo.mutate(
            runtime.id,
            lambda r: r.model_copy(update={"backend_state": backend.dump_state()}),
        )
        return self._repo.get(runtime.id)  # type: ignore[return-value]

    def remove_runtime(self, ref: str, *, force: bool = False) -> None:
        runtime = _resolver.resolve(self._repo.list(), ref)
        if runtime.used_by and not force:
            raise ConflictError(
                f"Runtime {runtime.id!r} is used by agents: {runtime.used_by}"
            )
        self._bind_storage_mounts(runtime, add=False)
        self._service.remove_runtime(runtime.id)

    def list_runtimes(self) -> list[Runtime]:
        return self._service.list_runtimes()

    def get_runtime(self, ref: str) -> Runtime:
        return _resolver.resolve(self._repo.list(), ref)

    def get_runtime_backend(self, ref: str):
        runtime = _resolver.resolve(self._repo.list(), ref)
        return self._service.get_runtime_backend(runtime.id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bind_storage_mounts(self, runtime: Runtime, *, add: bool) -> None:
        """Update Storage.mounted_by when a runtime is created or removed."""
        for mount in runtime.spec.mounts:
            if not isinstance(mount, RuntimeStorageMount):
                continue
            storage = self._storage_repo.get(mount.storage_id)
            if storage is None:
                continue
            self._storage_repo.mutate(
                mount.storage_id,
                lambda s, rid=runtime.id, adding=add: s.model_copy(
                    update={
                        "mounted_by": (
                            list(set(s.mounted_by) | {rid})
                            if adding
                            else [x for x in s.mounted_by if x != rid]
                        )
                    }
                ),
            )
