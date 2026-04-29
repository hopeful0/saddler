from __future__ import annotations

from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel

from ...agent.harness import Harness, register_harness_adapter
from ...agent.model import AgentSpec, RuleSpec, SkillSpec
from ...runtime.backend import ProcessHandle, RuntimeBackend, exec_capture
from .utils import (
    fetch_and_copy_skill_dir,
    fetch_rule_content,
    list_managed_sections,
    require_ok_exec,
    upsert_managed_section,
)


def _gemini_config_dir(workdir: str) -> str:
    return str(PurePosixPath(workdir) / ".gemini")


def _gemini_md(workdir: str) -> str:
    return str(PurePosixPath(workdir) / "GEMINI.md")


class GeminiHarnessConfig(BaseModel):
    binary: str = "gemini"


@register_harness_adapter("gemini")
class GeminiHarness(Harness):
    def __init__(self, spec: AgentSpec, config: GeminiHarnessConfig) -> None:
        self.spec = spec
        self.config = config

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> Self:
        harness_spec = spec.harness_spec
        if harness_spec is None:
            config = GeminiHarnessConfig()
        else:
            if not isinstance(harness_spec, dict):
                raise ValueError("gemini harness_spec must be a JSON object")
            config = GeminiHarnessConfig.model_validate(harness_spec)
        return cls(spec=spec, config=config)

    def is_installed(self, runtime: RuntimeBackend) -> bool:
        result = exec_capture(runtime, ["which", self.config.binary], self.spec.workdir)
        return result.exit_code == 0

    def install(self, runtime: RuntimeBackend) -> None:
        require_ok_exec(
            runtime,
            "npm install -g @google/gemini-cli",
            self.spec.workdir,
        )

    def install_rules(self, runtime: RuntimeBackend, rules: list[RuleSpec]) -> None:
        for rule in rules:
            upsert_managed_section(
                runtime,
                _gemini_md(self.spec.workdir),
                rule.name,
                fetch_rule_content(rule),
                self.spec.workdir,
            )

    def install_skills(self, runtime: RuntimeBackend, skills: list[SkillSpec]) -> None:
        cfg = _gemini_config_dir(self.spec.workdir)
        skills_dir = f"{cfg}/skills"
        require_ok_exec(runtime, f"mkdir -p {skills_dir}", self.spec.workdir)
        for skill in skills:
            fetch_and_copy_skill_dir(
                runtime, skill, f"{skills_dir}/{skill.name}", self.spec.workdir
            )

    def list_rules(self, runtime: RuntimeBackend) -> list[str]:
        return list_managed_sections(
            runtime, _gemini_md(self.spec.workdir), self.spec.workdir
        )

    def list_skills(self, runtime: RuntimeBackend) -> list[str]:
        cfg = _gemini_config_dir(self.spec.workdir)
        result = exec_capture(
            runtime,
            f"ls -1 {cfg}/skills 2>/dev/null || true",
            self.spec.workdir,
        )
        return [line for line in result.stdout.splitlines() if line]

    def tui(self, runtime: RuntimeBackend, *, tty: bool) -> ProcessHandle:
        proc = runtime.exec(
            [self.config.binary],
            cwd=self.spec.workdir,
            stdin=True,
            stdout=True,
            tty=tty,
        )
        if proc is None:
            raise RuntimeError("exec returned None for non-detached process")
        return proc

    def acp(self, runtime: RuntimeBackend, *, tty: bool) -> ProcessHandle:
        proc = runtime.exec(
            [self.config.binary, "--acp"],
            cwd=self.spec.workdir,
            stdin=True,
            stdout=True,
            tty=tty,
        )
        if proc is None:
            raise RuntimeError("exec returned None for non-detached process")
        return proc
