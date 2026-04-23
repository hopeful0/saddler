from __future__ import annotations

from dataclasses import dataclass, field

from ..agent.model import Agent, AgentSpec, RoleSpec, RuleSpec, SkillSpec
from ..app.agent import AgentUseCase


@dataclass(frozen=True)
class ResourceCreateSpec:
    """Parsed resource entry from CLI (name@source)."""

    name: str
    source: str
    path: str | None = None


@dataclass(frozen=True)
class AgentCreateRequest:
    runtime_ref: str
    harness: str
    workdir: str
    role: ResourceCreateSpec | None = None
    skills: list[ResourceCreateSpec] = field(default_factory=list)
    rules: list[ResourceCreateSpec] = field(default_factory=list)
    harness_spec: dict | None = None
    name: str | None = None
    metadata: dict[str, str] | None = None


class AgentApiService:
    def __init__(self, use_case: AgentUseCase) -> None:
        self._uc = use_case

    def create(self, req: AgentCreateRequest) -> Agent:
        spec = AgentSpec(
            harness=req.harness,
            workdir=req.workdir,
            role=_to_role(req.role),
            skills=[_to_skill(s) for s in req.skills],
            rules=[_to_rule(r) for r in req.rules],
            harness_spec=req.harness_spec,
        )
        return self._uc.create_agent(
            req.runtime_ref, spec, name=req.name, metadata=req.metadata
        )

    def remove(self, ref: str) -> None:
        self._uc.remove_agent(ref)

    def list(self) -> list[Agent]:
        return self._uc.list_agents()

    def inspect(self, ref: str) -> Agent:
        return self._uc.get_agent(ref)

    def tui(self, ref: str) -> None:
        self._uc.tui(ref)

    def acp(self, ref: str) -> None:
        self._uc.acp(ref)


def _to_role(spec: ResourceCreateSpec | None) -> RoleSpec | None:
    if spec is None:
        return None
    return RoleSpec(name="role", source=spec.source, path=spec.path)


def _to_skill(spec: ResourceCreateSpec) -> SkillSpec:
    return SkillSpec(name=spec.name, source=spec.source, path=spec.path)


def _to_rule(spec: ResourceCreateSpec) -> RuleSpec:
    return RuleSpec(name=spec.name, source=spec.source, path=spec.path)
