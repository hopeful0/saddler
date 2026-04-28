from __future__ import annotations

from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel

from ...agent.harness import Harness, register_harness_adapter
from ...agent.model import AgentSpec, RuleSpec, SkillSpec
from ...runtime.backend import RuntimeBackend
from .utils import fetch_and_copy_rule, fetch_and_copy_skill_dir, require_ok_exec


def _claude_config_dir(workdir: str) -> str:
    return str(PurePosixPath(workdir) / ".claude")


class ClaudeCodeHarnessConfig(BaseModel):
    binary: str = "claude"


@register_harness_adapter("claude-code")
class ClaudeCodeHarness(Harness):
    def __init__(self, spec: AgentSpec, config: ClaudeCodeHarnessConfig) -> None:
        self.spec = spec
        self.config = config

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> Self:
        harness_spec = spec.harness_spec
        if harness_spec is None:
            config = ClaudeCodeHarnessConfig()
        else:
            if not isinstance(harness_spec, dict):
                raise ValueError("claude-code harness_spec must be a JSON object")
            config = ClaudeCodeHarnessConfig.model_validate(harness_spec)
        return cls(spec=spec, config=config)

    def is_installed(self, runtime: RuntimeBackend) -> bool:
        result = runtime.exec(["which", self.config.binary], self.spec.workdir)
        return result.exit_code == 0

    def install(self, runtime: RuntimeBackend) -> None:
        require_ok_exec(
            runtime,
            "curl -fsSL https://claude.ai/install.sh | bash",
            self.spec.workdir,
        )

    def install_rules(self, runtime: RuntimeBackend, rules: list[RuleSpec]) -> None:
        cfg = _claude_config_dir(self.spec.workdir)
        rules_dir = f"{cfg}/rules"
        claude_md = f"{self.spec.workdir}/CLAUDE.md"
        require_ok_exec(
            runtime,
            f"mkdir -p {rules_dir}",
            self.spec.workdir,
        )
        for rule in rules:
            rule_file = f"{rules_dir}/{rule.name}.md"
            fetch_and_copy_rule(runtime, rule, rule_file)
            # Add @import to CLAUDE.md if not already present (paths are relative to CLAUDE.md location)
            import_line = f"@.claude/rules/{rule.name}.md"
            require_ok_exec(
                runtime,
                f"grep -qF '{import_line}' {claude_md} 2>/dev/null"
                f" || printf '\\n{import_line}\\n' >> {claude_md}",
                self.spec.workdir,
            )

    def install_skills(self, runtime: RuntimeBackend, skills: list[SkillSpec]) -> None:
        cfg = _claude_config_dir(self.spec.workdir)
        skills_dir = f"{cfg}/skills"
        require_ok_exec(
            runtime,
            f"mkdir -p {skills_dir}",
            self.spec.workdir,
        )
        for skill in skills:
            fetch_and_copy_skill_dir(
                runtime, skill, f"{skills_dir}/{skill.name}", self.spec.workdir
            )

    def list_rules(self, runtime: RuntimeBackend) -> list[str]:
        cfg = _claude_config_dir(self.spec.workdir)
        result = runtime.exec(
            f"ls -1 {cfg}/rules 2>/dev/null || true",
            self.spec.workdir,
        )
        return [line for line in result.stdout.splitlines() if line]

    def list_skills(self, runtime: RuntimeBackend) -> list[str]:
        cfg = _claude_config_dir(self.spec.workdir)
        result = runtime.exec(
            f"ls -1 {cfg}/skills 2>/dev/null || true",
            self.spec.workdir,
        )
        return [line for line in result.stdout.splitlines() if line]

    def tui(self, runtime: RuntimeBackend) -> None:
        runtime.exec_fg([self.config.binary], cwd=self.spec.workdir)

    def acp(self, runtime: RuntimeBackend) -> None:
        if runtime.exec("which claude-agent-acp", self.spec.workdir).exit_code != 0:
            require_ok_exec(
                runtime,
                "npm install -g @agentclientprotocol/claude-agent-acp",
                self.spec.workdir,
            )
        runtime.exec_fg("claude-agent-acp", cwd=self.spec.workdir)
