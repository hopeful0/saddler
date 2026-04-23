from .harness import Harness, get_harness_cls
from ..runtime.backend import RuntimeBackend
from ..shared.repository import Repository
from ..shared.utils import generate_id
from .model import Agent, AgentSpec


class AgentService:
    def __init__(
        self,
        repository: Repository[Agent],
    ):
        self.repository = repository

    def create_agent(
        self,
        runtime_id: str,
        spec: AgentSpec,
        runtime: RuntimeBackend,
        *,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Agent:
        aid = generate_id()

        harness = get_harness_cls(spec.harness).from_spec(spec)

        if not harness.is_installed(runtime):
            harness.install(runtime)

        rules = ([spec.role] if spec.role else []) + spec.rules
        if rules:
            harness.install_rules(runtime, rules)

        if spec.skills:
            harness.install_skills(runtime, spec.skills)

        agent = Agent(
            id=aid,
            name=name,
            metadata=metadata,
            runtime=runtime_id,
            spec=spec,
        )
        self.repository.insert(agent)
        return agent

    def remove_agent(self, id: str) -> None:
        rec = self.repository.get(id)
        if rec is None:
            return
        self.repository.delete(id)

    def list_agents(self) -> list[Agent]:
        return self.repository.list()

    def get_agent(self, id: str) -> Agent | None:
        return self.repository.get(id)

    def get_harness(self, id: str) -> Harness:
        agent = self.repository.get(id)
        if agent is None:
            raise ValueError(f"Agent not found: {id}")
        return get_harness_cls(agent.spec.harness).from_spec(agent.spec)
