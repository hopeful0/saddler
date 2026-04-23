from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ...agent.model import RuleSpec, SkillSpec
from ...resource import parse_source
from ...resource.model import SourceSpec
from ...runtime.backend import RuntimeBackend


def require_ok_exec(runtime: RuntimeBackend, command: list[str], cwd: str) -> None:
    result = runtime.exec(command, cwd)
    if result.exit_code != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "").strip() or "runtime exec failed"
        )


def resource_source_uri(source: str | SourceSpec) -> str:
    if isinstance(source, SourceSpec):
        return source.uri
    return source


def fetch_rule_content(rule: RuleSpec) -> str:
    fetcher_cls, source_spec = parse_source(resource_source_uri(rule.source))
    fetcher = fetcher_cls()
    with fetcher.fetch_source(source_spec) as source_root:
        with fetcher.fetch_resource(rule, source_root) as resource_path:
            return resource_path.read_text(encoding="utf-8")


def write_content_to_runtime(
    runtime: RuntimeBackend, content: str, dest_path: str
) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".md", delete=False
    ) as tmp:
        tmp.write(content)
        tmp_host = tmp.name
    try:
        runtime.copy_to(tmp_host, dest_path)
    finally:
        Path(tmp_host).unlink(missing_ok=True)


def fetch_and_copy_rule(
    runtime: RuntimeBackend,
    rule: RuleSpec,
    dest_path: str,
) -> None:
    write_content_to_runtime(runtime, fetch_rule_content(rule), dest_path)


def list_managed_sections(
    runtime: RuntimeBackend, file_path: str, cwd: str
) -> list[str]:
    result = runtime.exec(["cat", file_path], cwd)
    if result.exit_code != 0:
        return []
    return re.findall(r"<!-- saddler:(.+?):start -->", result.stdout)


def upsert_managed_section(
    runtime: RuntimeBackend,
    file_path: str,
    section_name: str,
    content: str,
    cwd: str,
) -> None:
    """Insert or replace a named block in a file, preserving surrounding content.

    The block is wrapped with HTML comment markers:
        <!-- saddler:section_name:start -->
        ...content...
        <!-- saddler:section_name:end -->
    """
    start_marker = f"<!-- saddler:{section_name}:start -->"
    end_marker = f"<!-- saddler:{section_name}:end -->"
    block = f"{start_marker}\n{content.strip()}\n{end_marker}"

    result = runtime.exec(["cat", file_path], cwd)
    existing = result.stdout if result.exit_code == 0 else ""

    if start_marker in existing:
        pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
        new_content = re.sub(pattern, block, existing, flags=re.DOTALL)
    else:
        sep = "\n\n" if existing.strip() else ""
        new_content = existing.rstrip() + sep + block + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".md", delete=False
    ) as tmp:
        tmp.write(new_content)
        tmp_host = tmp.name
    try:
        runtime.copy_to(tmp_host, file_path)
    finally:
        Path(tmp_host).unlink(missing_ok=True)


def fetch_and_copy_skill_dir(
    runtime: RuntimeBackend,
    skill: SkillSpec,
    dest_dir: str,
    cwd: str,
) -> None:
    fetcher_cls, source_spec = parse_source(resource_source_uri(skill.source))
    fetcher = fetcher_cls()
    with fetcher.fetch_source(source_spec) as source_root:
        with fetcher.fetch_resource(skill, source_root) as resource_path:
            skill_root = (
                resource_path if resource_path.is_dir() else resource_path.parent
            )
            require_ok_exec(runtime, ["sh", "-lc", f"rm -rf {dest_dir}"], cwd)
            runtime.copy_to(str(skill_root), dest_dir)
