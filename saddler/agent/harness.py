from __future__ import annotations
from typing import Protocol, Self

from .model import AgentSpec, RuleSpec, SkillSpec
from ..runtime.backend import ProcessHandle, RuntimeBackend
from ..shared.registry import Registry


class Harness(Protocol):
    """Harness integration (CLI install, rules/skills, TUI/ACP)."""

    def is_installed(self, runtime: RuntimeBackend) -> bool: ...

    def install(self, runtime: RuntimeBackend) -> None: ...

    def install_rules(self, runtime: RuntimeBackend, rules: list[RuleSpec]) -> None: ...

    def install_skills(
        self, runtime: RuntimeBackend, skills: list[SkillSpec]
    ) -> None: ...

    def list_skills(self, runtime: RuntimeBackend) -> list[str]: ...

    def list_rules(self, runtime: RuntimeBackend) -> list[str]: ...

    def tui(self, runtime: RuntimeBackend, *, tty: bool) -> ProcessHandle: ...

    def acp(self, runtime: RuntimeBackend, *, tty: bool) -> ProcessHandle: ...

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> Self: ...


AGENT_HARNESS_REGISTRY = Registry[type[Harness]](group="saddler.agent.harness")


def register_harness_adapter(harness_type: str):
    """Decorator to register a HarnessAdapter class into the registry."""

    def wrapper(cls: type[Harness]) -> type[Harness]:
        AGENT_HARNESS_REGISTRY.register(harness_type, cls)
        return cls

    return wrapper


def get_harness_cls(harness_type: str) -> type[Harness]:
    return AGENT_HARNESS_REGISTRY.get(harness_type)
