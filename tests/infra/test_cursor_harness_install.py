from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from saddler.agent.model import AgentSpec, RuleSpec, SkillSpec
from saddler.infra.harness.cursor import (
    CursorHarness,
    _ensure_rule_frontmatter,
)
from saddler.runtime.backend import Command, ProcessHandle, RuntimeBackend

# Ensure infra fetchers are registered.
from saddler import infra as _infra  # noqa: F401


class FakeRuntimeBackend(RuntimeBackend):
    """Maps agent workdir `/workspace` to a host directory for tests."""

    def __init__(self, workdir_root: Path) -> None:
        self._workdir_root = workdir_root
        self._workspace = workdir_root / "workspace"

    def _resolve_cwd(self, cwd: str) -> Path:
        if cwd == "/workspace":
            return self._workspace
        raise AssertionError(f"unexpected cwd: {cwd!r}")

    def _resolve_dest(self, dest_runtime: str) -> Path:
        if dest_runtime.startswith("/workspace/"):
            return self._workspace / dest_runtime[len("/workspace/") :]
        if dest_runtime == "/workspace":
            return self._workspace
        raise AssertionError(f"unexpected dest: {dest_runtime!r}")

    def runtime_path(self, relative_under_workspace: str) -> Path:
        rel = relative_under_workspace.lstrip("/")
        return self._workspace / rel

    def read_runtime_text(self, relative_under_workspace: str) -> str:
        return self.runtime_path(relative_under_workspace).read_text(encoding="utf-8")

    @classmethod
    def create(cls, spec):  # noqa: ANN001
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def remove(self) -> None:
        raise NotImplementedError

    def is_running(self) -> bool:
        return True

    def _rewrite_sh_script(self, command: Command) -> list[str]:
        """Map `/workspace` in `sh -lc` scripts to the host workspace directory."""
        if isinstance(command, str):
            return ["sh", "-lc", command.replace("/workspace", str(self._workspace))]
        if len(command) >= 3 and command[0] == "sh" and command[1] == "-lc":
            script = command[2].replace("/workspace", str(self._workspace))
            return ["sh", "-lc", script]
        return command

    def exec(
        self,
        command: Command,
        cwd: str,
        env: dict[str, str] | None = None,
        *,
        stdin: bool = False,
        stdout: bool = False,
        stderr: bool = False,
        tty: bool = False,
        detach: bool = False,
        timeout: float | None = None,
    ) -> ProcessHandle | None:
        host_cwd = self._resolve_cwd(cwd)
        host_cwd.mkdir(parents=True, exist_ok=True)
        if detach:
            subprocess.Popen(
                self._rewrite_sh_script(command),
                cwd=str(host_cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return None
        proc = subprocess.Popen(
            self._rewrite_sh_script(command),
            cwd=str(host_cwd),
            env=env,
            stdin=subprocess.PIPE if stdin else None,
            stdout=subprocess.PIPE if stdout else None,
            stderr=subprocess.PIPE if stderr else None,
        )
        return proc  # type: ignore[return-value]

    def copy_to(self, src_host: str, dest_runtime: str) -> None:
        dest = self._resolve_dest(dest_runtime)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = Path(src_host)
        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)

    def copy_from(self, src_runtime: str, dest_host: str) -> None:
        raise NotImplementedError

    def dump_state(self):
        return None

    @classmethod
    def load_state(cls, spec, state):  # noqa: ANN001
        raise NotImplementedError


def test_rule_without_frontmatter_gets_default_frontmatter() -> None:
    content = "## Body\nrule content\n"
    normalized = _ensure_rule_frontmatter(content)
    assert normalized.startswith("---\n")
    assert "alwaysApply: true" in normalized
    assert "## Body" in normalized


def test_rule_frontmatter_missing_alwaysapply_gets_patched() -> None:
    content = "---\ndescription: demo\nglobs:\n---\nbody\n"
    normalized = _ensure_rule_frontmatter(content)
    assert "alwaysApply: true" in normalized
    assert "body" in normalized


def test_rule_frontmatter_with_alwaysapply_is_preserved() -> None:
    content = "---\ndescription: x\nalwaysApply: false\n---\nbody\n"
    normalized = _ensure_rule_frontmatter(content)
    assert normalized == content


def test_install_rules_fetches_resource_and_writes_mdc(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "rules").mkdir(parents=True)
    (source / "rules" / "role.mdc").write_text("body\n", encoding="utf-8")
    spec = AgentSpec(
        harness="cursor",
        workdir="/workspace",
        role=None,
        skills=[],
        rules=[RuleSpec(name="role", source=str(source), path="rules/role.mdc")],
        harness_spec=None,
    )
    runtime = FakeRuntimeBackend(workdir_root=tmp_path / "runtime")
    harness = CursorHarness.from_spec(spec)
    harness.install_rules(runtime, spec.rules)
    written = runtime.read_runtime_text(".cursor/rules/role.mdc")
    assert "alwaysApply: true" in written
    assert "body" in written


def test_install_skills_copies_whole_skill_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    skill_dir = source / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\n",
        encoding="utf-8",
    )
    (skill_dir / "guide.md").write_text("guide", encoding="utf-8")
    spec = AgentSpec(
        harness="cursor",
        workdir="/workspace",
        role=None,
        skills=[SkillSpec(name="demo", source=str(source), path="skills/demo")],
        rules=[],
        harness_spec=None,
    )
    runtime = FakeRuntimeBackend(workdir_root=tmp_path / "runtime")
    harness = CursorHarness.from_spec(spec)
    harness.install_skills(runtime, spec.skills)
    assert runtime.runtime_path(".cursor/skills/demo/SKILL.md").exists()
    assert runtime.runtime_path(".cursor/skills/demo/guide.md").exists()


def test_from_spec_defaults_binary_when_harness_spec_missing() -> None:
    spec = AgentSpec(
        harness="cursor",
        workdir="/workspace",
        role=None,
        skills=[],
        rules=[],
        harness_spec=None,
    )
    harness = CursorHarness.from_spec(spec)
    assert harness.config.binary == "cursor-agent"


def test_from_spec_supports_custom_binary() -> None:
    spec = AgentSpec(
        harness="cursor",
        workdir="/workspace",
        role=None,
        skills=[],
        rules=[],
        harness_spec={"binary": "cursor-custom"},
    )
    harness = CursorHarness.from_spec(spec)
    assert harness.config.binary == "cursor-custom"


def test_from_spec_rejects_non_object_harness_spec() -> None:
    spec = AgentSpec(
        harness="cursor",
        workdir="/workspace",
        role=None,
        skills=[],
        rules=[],
        harness_spec="invalid",
    )
    with pytest.raises(ValueError, match="cursor harness_spec must be a JSON object"):
        CursorHarness.from_spec(spec)


def test_is_installed_reflects_binary_presence(tmp_path: Path) -> None:
    runtime = FakeRuntimeBackend(workdir_root=tmp_path / "runtime")
    installed = CursorHarness.from_spec(
        AgentSpec(
            harness="cursor",
            workdir="/workspace",
            role=None,
            skills=[],
            rules=[],
            harness_spec={"binary": "sh"},
        )
    )
    missing = CursorHarness.from_spec(
        AgentSpec(
            harness="cursor",
            workdir="/workspace",
            role=None,
            skills=[],
            rules=[],
            harness_spec={"binary": "binary-that-does-not-exist"},
        )
    )
    assert installed.is_installed(runtime) is True
    assert missing.is_installed(runtime) is False


def test_list_rules_and_skills_returns_empty_when_not_installed(
    tmp_path: Path,
) -> None:
    spec = AgentSpec(
        harness="cursor",
        workdir="/workspace",
        role=None,
        skills=[],
        rules=[],
        harness_spec=None,
    )
    runtime = FakeRuntimeBackend(workdir_root=tmp_path / "runtime")
    harness = CursorHarness.from_spec(spec)
    assert harness.list_rules(runtime) == []
    assert harness.list_skills(runtime) == []


def test_list_rules_and_skills_returns_installed_names(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "rules").mkdir(parents=True)
    (source / "rules" / "role.mdc").write_text("body\n", encoding="utf-8")
    skill_dir = source / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\n",
        encoding="utf-8",
    )

    spec = AgentSpec(
        harness="cursor",
        workdir="/workspace",
        role=None,
        skills=[SkillSpec(name="demo", source=str(source), path="skills/demo")],
        rules=[RuleSpec(name="role", source=str(source), path="rules/role.mdc")],
        harness_spec=None,
    )
    runtime = FakeRuntimeBackend(workdir_root=tmp_path / "runtime")
    harness = CursorHarness.from_spec(spec)
    harness.install_rules(runtime, spec.rules)
    harness.install_skills(runtime, spec.skills)

    assert harness.list_rules(runtime) == ["role.mdc"]
    assert harness.list_skills(runtime) == ["demo"]


def test_tui_and_acp_use_unified_exec_error_behavior(tmp_path: Path) -> None:
    runtime = FakeRuntimeBackend(workdir_root=tmp_path / "runtime")
    ok_harness = CursorHarness.from_spec(
        AgentSpec(
            harness="cursor",
            workdir="/workspace",
            role=None,
            skills=[],
            rules=[],
            harness_spec={"binary": "true"},
        )
    )
    ok_harness.tui(runtime)
    ok_harness.acp(runtime)

    failed_harness = CursorHarness.from_spec(
        AgentSpec(
            harness="cursor",
            workdir="/workspace",
            role=None,
            skills=[],
            rules=[],
            harness_spec={"binary": "false"},
        )
    )
    with pytest.raises(RuntimeError):
        failed_harness.tui(runtime)
    with pytest.raises(RuntimeError):
        failed_harness.acp(runtime)
