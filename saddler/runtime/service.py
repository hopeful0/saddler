from ..shared.repository import Repository
from ..shared.utils import generate_id
from .backend import RuntimeBackend, get_runtime_backend_cls
from .model import (
    Runtime,
    RuntimeSpec,
)


class RuntimeService:
    def __init__(
        self,
        repository: Repository[Runtime],
    ):
        self.repository = repository

    def create_runtime(
        self,
        spec: RuntimeSpec,
        *,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Runtime:
        runtime_id = generate_id()

        backend = get_runtime_backend_cls(spec.backend_type).create(spec)
        state = backend.dump_state()

        runtime = Runtime(
            id=runtime_id,
            name=name,
            metadata=metadata,
            spec=spec,
            backend_state=state,
            used_by=[],
        )
        self.repository.insert(runtime)
        return runtime

    def remove_runtime(self, id: str) -> None:
        runtime = self.repository.get(id)
        if runtime is None:
            return

        if runtime.used_by:
            raise RuntimeError(f"Runtime is in use by {runtime.used_by}")

        backend = get_runtime_backend_cls(runtime.spec.backend_type).load_state(
            runtime.spec, runtime.backend_state
        )
        backend.remove()
        self.repository.delete(id)

    def list_runtimes(self) -> list[Runtime]:
        return self.repository.list()

    def get_runtime(self, id: str) -> Runtime | None:
        return self.repository.get(id)

    def get_runtime_backend(self, id: str) -> RuntimeBackend:
        runtime = self.repository.get(id)
        if runtime is None:
            raise ValueError(f"Runtime not found: {id}")
        return get_runtime_backend_cls(runtime.spec.backend_type).load_state(
            runtime.spec, runtime.backend_state
        )
