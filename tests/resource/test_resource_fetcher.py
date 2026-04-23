from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import pytest

from saddler.resource import (
    ResourceSpec,
    get_fetcher_cls,
    parse_source,
    register_fetcher,
)
from saddler.infra.fetcher.utils import (
    find_resource,
    find_role,
    find_skill,
)
from saddler.resource.model import SourceSpec


def test_parse_source_uses_local_fetcher_when_path_exists(tmp_path: Path) -> None:
    existing = tmp_path / "source"
    existing.mkdir()
    fetcher_cls, spec = parse_source(str(existing))
    assert fetcher_cls.__name__ == "LocalFetcher"
    assert spec.kind == "local"


def test_parse_source_raises_when_no_fetcher_matches() -> None:
    with pytest.raises(ValueError):
        parse_source("/tmp/not-exists-for-local-fetcher")


def test_parse_source_prefers_smaller_priority_fetcher() -> None:
    @register_fetcher("low-priority-test")
    class LowPriorityFetcher:
        priority = 20

        @classmethod
        def parse_source(cls, source: str) -> SourceSpec | None:
            if source == "prio://demo":
                return SourceSpec(kind="prio-low", uri=source)
            return None

        @contextmanager
        def fetch_source(self, spec: SourceSpec) -> Generator[Path, None, None]:
            yield Path(".")

    @register_fetcher("high-priority-test")
    class HighPriorityFetcher:
        priority = 1

        @classmethod
        def parse_source(cls, source: str) -> SourceSpec | None:
            if source == "prio://demo":
                return SourceSpec(kind="prio-high", uri=source)
            return None

        @contextmanager
        def fetch_source(self, spec: SourceSpec) -> Generator[Path, None, None]:
            yield Path(".")

    fetcher_cls, _ = parse_source("prio://demo")
    assert fetcher_cls is HighPriorityFetcher


def test_get_fetcher_cls_returns_registered_fetcher() -> None:
    @register_fetcher("structured-source-test")
    class StructuredSourceFetcher:
        priority = 5

        @classmethod
        def parse_source(cls, source: str) -> SourceSpec | None:
            return None

        @contextmanager
        def fetch_source(self, spec: SourceSpec) -> Generator[Path, None, None]:
            yield Path(".")

    fetcher_cls = get_fetcher_cls("structured-source-test")
    assert fetcher_cls is StructuredSourceFetcher


def test_resource_spec_supports_optional_hash(tmp_path: Path) -> None:
    spec = ResourceSpec(
        kind="skill",
        name="demo",
        source=str(tmp_path),
        hash="sha256:abc",
    )
    assert spec.hash == "sha256:abc"


def test_resource_spec_supports_structured_source() -> None:
    spec = ResourceSpec(
        kind="rule",
        name="r1",
        source=SourceSpec(kind="local", uri="/tmp/rules"),
    )
    assert isinstance(spec.source, SourceSpec)


def test_find_skill_resolves_skill_md_with_frontmatter(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\nbody\n", encoding="utf-8"
    )

    skill_spec = ResourceSpec(
        kind="skill",
        name="demo",
        source=str(tmp_path),
    )
    assert find_skill(skill_spec, tmp_path).name == "SKILL.md"


def test_find_role_resolves_markdown_text_file(tmp_path: Path) -> None:
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "base.md").write_text("plain text role", encoding="utf-8")

    role_spec = ResourceSpec(kind="rule", name="base", source=str(tmp_path))
    assert find_role(role_spec, tmp_path).name == "base.md"


def test_find_resource_dispatches_by_kind(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\nbody\n", encoding="utf-8"
    )
    skill_spec = ResourceSpec(kind="skill", name="demo", source=str(tmp_path))
    assert find_resource(skill_spec, tmp_path).name == "SKILL.md"


def test_find_skill_kind_mismatch_raises(tmp_path: Path) -> None:
    rule_spec = ResourceSpec(
        kind="rule",
        name="base",
        source=str(tmp_path),
    )
    with pytest.raises(ValueError, match="spec kind mismatch"):
        find_skill(rule_spec, tmp_path)


def test_find_skill_requires_frontmatter_fields(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\n---\nbody\n", encoding="utf-8"
    )
    spec = ResourceSpec(kind="skill", name="demo", source=str(tmp_path))
    with pytest.raises(ValueError, match="description"):
        find_skill(spec, tmp_path)
