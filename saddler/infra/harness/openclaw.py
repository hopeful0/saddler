from __future__ import annotations

import time
from pathlib import PurePosixPath
from typing import Self

from pydantic import BaseModel

from ...agent.harness import Harness, register_harness_adapter
from ...agent.model import AgentSpec, RuleSpec, SkillSpec
from ...runtime.backend import ProcessHandle, RuntimeBackend, exec_bg, exec_capture
from .utils import (
    fetch_and_copy_skill_dir,
    fetch_rule_content,
    list_managed_sections,
    require_ok_exec,
    upsert_managed_section,
)


def _agents_md(workdir: str) -> str:
    return str(PurePosixPath(workdir) / "AGENTS.md")


class OpenClawHarnessConfig(BaseModel):
    binary: str = "openclaw"
    gateway_wait_timeout_seconds: float = 30.0
    gateway_poll_interval_seconds: float = 0.2


@register_harness_adapter("openclaw")
class OpenClawHarness(Harness):
    def __init__(self, spec: AgentSpec, config: OpenClawHarnessConfig) -> None:
        self.spec = spec
        self.config = config

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> Self:
        harness_spec = spec.harness_spec
        if harness_spec is None:
            config = OpenClawHarnessConfig()
        else:
            if not isinstance(harness_spec, dict):
                raise ValueError("openclaw harness_spec must be a JSON object")
            config = OpenClawHarnessConfig.model_validate(harness_spec)
        return cls(spec=spec, config=config)

    def is_installed(self, runtime: RuntimeBackend) -> bool:
        result = exec_capture(
            runtime, [self.config.binary, "--version"], self.spec.workdir
        )
        return result.exit_code == 0 and "OpenClaw" in result.stdout

    def install(self, runtime: RuntimeBackend) -> None:
        require_ok_exec(
            runtime,
            "npm install -g openclaw",
            self.spec.workdir,
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
        skills_dir = str(PurePosixPath(self.spec.workdir) / "skills")
        require_ok_exec(runtime, f"mkdir -p {skills_dir}", self.spec.workdir)
        for skill in skills:
            fetch_and_copy_skill_dir(
                runtime, skill, f"{skills_dir}/{skill.name}", self.spec.workdir
            )

    def list_rules(self, runtime: RuntimeBackend) -> list[str]:
        return list_managed_sections(
            runtime, _agents_md(self.spec.workdir), self.spec.workdir
        )

    def list_skills(self, runtime: RuntimeBackend) -> list[str]:
        skills_dir = str(PurePosixPath(self.spec.workdir) / "skills")
        result = exec_capture(
            runtime,
            f"ls -1 {skills_dir} 2>/dev/null || true",
            self.spec.workdir,
        )
        return [line for line in result.stdout.splitlines() if line]

    def tui(self, runtime: RuntimeBackend, *, tty: bool) -> ProcessHandle:
        self._ensure_gateway(runtime)
        proc = runtime.exec(
            [self.config.binary, "tui"],
            cwd=self.spec.workdir,
            stdin=True,
            stdout=True,
            tty=tty,
        )
        if proc is None:
            raise RuntimeError("exec returned None for non-detached process")
        return proc

    def acp(self, runtime: RuntimeBackend, *, tty: bool) -> ProcessHandle:
        self._ensure_gateway(runtime)
        proc = runtime.exec(
            [self.config.binary, "acp"],
            cwd=self.spec.workdir,
            stdin=True,
            stdout=True,
            tty=tty,
        )
        if proc is None:
            raise RuntimeError("exec returned None for non-detached process")
        return proc

    def _ensure_gateway(self, runtime: RuntimeBackend) -> None:
        status_cmd = [self.config.binary, "gateway", "status", "--require-rpc"]
        if exec_capture(runtime, status_cmd, self.spec.workdir).exit_code == 0:
            return
        exec_bg(
            runtime,
            [self.config.binary, "gateway", "--allow-unconfigured"],
            self.spec.workdir,
        )
        deadline = time.monotonic() + self.config.gateway_wait_timeout_seconds
        while time.monotonic() < deadline:
            if exec_capture(runtime, status_cmd, self.spec.workdir).exit_code == 0:
                return
            time.sleep(self.config.gateway_poll_interval_seconds)
        raise RuntimeError(
            "openclaw gateway did not become ready before timeout; "
            "run `openclaw gateway status --require-rpc` for details"
        )
