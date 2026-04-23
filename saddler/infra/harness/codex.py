from __future__ import annotations

from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel

from ...agent.harness import Harness, register_harness_adapter
from ...agent.model import AgentSpec, RuleSpec, SkillSpec
from ...runtime.backend import RuntimeBackend
from .utils import (
    fetch_and_copy_skill_dir,
    fetch_rule_content,
    list_managed_sections,
    require_ok_exec,
    upsert_managed_section,
)


def _codex_config_dir(workdir: str) -> str:
    return str(PurePosixPath(workdir) / ".codex")


def _agents_md(workdir: str) -> str:
    return str(PurePosixPath(workdir) / "AGENTS.md")


class CodexHarnessConfig(BaseModel):
    binary: str = "codex"


@register_harness_adapter("codex")
class CodexHarness(Harness):
    def __init__(self, spec: AgentSpec, config: CodexHarnessConfig) -> None:
        self.spec = spec
        self.config = config

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> Self:
        harness_spec = spec.harness_spec
        if harness_spec is None:
            config = CodexHarnessConfig()
        else:
            if not isinstance(harness_spec, dict):
                raise ValueError("codex harness_spec must be a JSON object")
            config = CodexHarnessConfig.model_validate(harness_spec)
        return cls(spec=spec, config=config)

    def is_installed(self, runtime: RuntimeBackend) -> bool:
        result = runtime.exec(["which", self.config.binary], self.spec.workdir)
        return result.exit_code == 0

    def install(self, runtime: RuntimeBackend) -> None:
        raise NotImplementedError(
            "Codex harness installation is not supported; provide the binary in the runtime"
        )

    def install_rules(self, runtime: RuntimeBackend, rules: list[RuleSpec]) -> None:
        for rule in rules:
            upsert_managed_section(
                runtime,
                _agents_md(self.spec.workdir),
                rule.name,
                fetch_rule_content(rule),
                self.spec.workdir,
            )

    def install_skills(self, runtime: RuntimeBackend, skills: list[SkillSpec]) -> None:
        cfg = _codex_config_dir(self.spec.workdir)
        prompts_dir = f"{cfg}/prompts"
        require_ok_exec(
            runtime, ["sh", "-lc", f"mkdir -p {prompts_dir}"], self.spec.workdir
        )
        for skill in skills:
            fetch_and_copy_skill_dir(
                runtime, skill, f"{prompts_dir}/{skill.name}", self.spec.workdir
            )

    def list_rules(self, runtime: RuntimeBackend) -> list[str]:
        return list_managed_sections(
            runtime, _agents_md(self.spec.workdir), self.spec.workdir
        )

    def list_skills(self, runtime: RuntimeBackend) -> list[str]:
        cfg = _codex_config_dir(self.spec.workdir)
        result = runtime.exec(
            ["sh", "-lc", f"ls -1 {cfg}/prompts 2>/dev/null || true"],
            self.spec.workdir,
        )
        return [line for line in result.stdout.splitlines() if line]

    def tui(self, runtime: RuntimeBackend) -> None:
        runtime.exec_fg([self.config.binary], cwd=self.spec.workdir)

    def acp(self, runtime: RuntimeBackend) -> None:
        raise NotImplementedError("Codex does not have a built-in acp command")
