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
    require_ok_exec,
    write_content_to_runtime,
)


def _cursor_config_dir(workdir: str) -> str:
    return str(PurePosixPath(workdir) / ".cursor")


def _inject_default_frontmatter(content: str) -> str:
    header = "---\nalwaysApply: true\n---\n"
    return header + content.lstrip()


def _ensure_rule_frontmatter(content: str) -> str:
    stripped = content.lstrip()
    if not stripped.startswith("---\n"):
        return _inject_default_frontmatter(content)

    lines = stripped.split("\n")
    if not lines or lines[0] != "---":
        return _inject_default_frontmatter(content)

    close_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i] == "---":
            close_idx = i
            break
    if close_idx is None:
        return _inject_default_frontmatter(content)

    fm_inner = lines[1:close_idx]
    if any(line.strip().startswith("alwaysApply:") for line in fm_inner):
        return content

    new_lines = [*lines[:close_idx], "alwaysApply: true", *lines[close_idx:]]
    return "\n".join(new_lines)


class CursorHarnessConfig(BaseModel):
    binary: str = "cursor-agent"


@register_harness_adapter("cursor")
class CursorHarness(Harness):
    def __init__(self, spec: AgentSpec, config: CursorHarnessConfig) -> None:
        self.spec = spec
        self.config = config

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> Self:
        harness_spec = spec.harness_spec
        if harness_spec is None:
            config = CursorHarnessConfig()
        else:
            if not isinstance(harness_spec, dict):
                raise ValueError("cursor harness_spec must be a JSON object")
            config = CursorHarnessConfig.model_validate(harness_spec)
        return cls(spec=spec, config=config)

    def is_installed(self, runtime: RuntimeBackend) -> bool:
        result = runtime.exec(["which", self.config.binary], self.spec.workdir)
        return result.exit_code == 0

    def install(self, runtime: RuntimeBackend) -> None:
        require_ok_exec(
            runtime,
            "curl https://cursor.com/install -fsS | bash",
            self.spec.workdir,
        )

    def install_rules(self, runtime: RuntimeBackend, rules: list[RuleSpec]) -> None:
        cfg = _cursor_config_dir(self.spec.workdir)
        require_ok_exec(
            runtime,
            f"mkdir -p {cfg}/rules",
            self.spec.workdir,
        )
        for rule in rules:
            normalized = _ensure_rule_frontmatter(fetch_rule_content(rule))
            write_content_to_runtime(
                runtime, normalized, f"{cfg}/rules/{rule.name}.mdc"
            )

    def install_skills(self, runtime: RuntimeBackend, skills: list[SkillSpec]) -> None:
        cfg = _cursor_config_dir(self.spec.workdir)
        require_ok_exec(
            runtime,
            f"mkdir -p {cfg}/skills",
            self.spec.workdir,
        )
        for skill in skills:
            fetch_and_copy_skill_dir(
                runtime, skill, f"{cfg}/skills/{skill.name}", self.spec.workdir
            )

    def list_skills(self, runtime: RuntimeBackend) -> list[str]:
        cfg = _cursor_config_dir(self.spec.workdir)
        result = runtime.exec(
            command=f"ls -1 {cfg}/skills 2>/dev/null || true",
            cwd=self.spec.workdir,
        )
        return [line for line in result.stdout.splitlines() if line]

    def list_rules(self, runtime: RuntimeBackend) -> list[str]:
        cfg = _cursor_config_dir(self.spec.workdir)
        result = runtime.exec(
            command=f"ls -1 {cfg}/rules 2>/dev/null || true",
            cwd=self.spec.workdir,
        )
        return [line for line in result.stdout.splitlines() if line]

    def tui(self, runtime: RuntimeBackend) -> None:
        runtime.exec_fg([self.config.binary], cwd=self.spec.workdir)

    def acp(self, runtime: RuntimeBackend) -> None:
        runtime.exec_fg([self.config.binary, "acp"], cwd=self.spec.workdir)
