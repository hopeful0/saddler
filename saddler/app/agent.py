from ..agent.model import Agent, AgentSpec
from ..agent.service import AgentService
from ..runtime.backend import RuntimeBackend
from ..runtime.model import Runtime
from ..shared.repository import Repository
from .errors import NotFoundError
from .resolver import NameResolver

_resolver = NameResolver[Agent]("Agent")
_runtime_resolver = NameResolver[Runtime]("Runtime")


class AgentUseCase:
    def __init__(
        self,
        service: AgentService,
        repository: Repository[Agent],
        runtime_repo: Repository[Runtime],
    ) -> None:
        self._service = service
        self._repo = repository
        self._runtime_repo = runtime_repo

    def create_agent(
        self,
        runtime_ref: str,
        spec: AgentSpec,
        *,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Agent:
        runtime = _runtime_resolver.resolve(self._runtime_repo.list(), runtime_ref)
        backend = self._get_backend(runtime)

        agent = self._service.create_agent(
            runtime.id,
            spec,
            backend,
            name=name,
            metadata=metadata,
        )

        self._runtime_repo.mutate(
            runtime.id,
            lambda r, aid=agent.id: r.model_copy(
                update={"used_by": list(set(r.used_by) | {aid})}
            ),
        )
        return agent

    def remove_agent(self, ref: str) -> None:
        agent = _resolver.resolve(self._repo.list(), ref)
        self._service.remove_agent(agent.id)
        self._runtime_repo.mutate(
            agent.runtime,
            lambda r, aid=agent.id: r.model_copy(
                update={"used_by": [x for x in r.used_by if x != aid]}
            ),
        )

    def list_agents(self) -> list[Agent]:
        return self._service.list_agents()

    def get_agent(self, ref: str) -> Agent:
        return _resolver.resolve(self._repo.list(), ref)

    def tui(self, ref: str) -> None:
        agent = _resolver.resolve(self._repo.list(), ref)
        runtime = self._runtime_repo.get(agent.runtime)
        if runtime is None:
            raise NotFoundError(
                f"Runtime {agent.runtime!r} not found for agent {agent.id!r}"
            )
        harness = self._service.get_harness(agent.id)
        harness.tui(self._get_backend(runtime))

    def acp(self, ref: str) -> None:
        agent = _resolver.resolve(self._repo.list(), ref)
        runtime = self._runtime_repo.get(agent.runtime)
        if runtime is None:
            raise NotFoundError(
                f"Runtime {agent.runtime!r} not found for agent {agent.id!r}"
            )
        harness = self._service.get_harness(agent.id)
        harness.acp(self._get_backend(runtime))

    # ------------------------------------------------------------------

    @staticmethod
    def _get_backend(runtime: Runtime) -> RuntimeBackend:
        from ..runtime.backend import get_runtime_backend_cls

        return get_runtime_backend_cls(runtime.spec.backend_type).load_state(
            runtime.spec, runtime.backend_state
        )
