from __future__ import annotations

from pathlib import Path

from ...resource.model import ResourceSpec


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"file is not valid utf-8 text: {path}") from exc


def _parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("skill file missing frontmatter")

    data: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return data
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("'\"")

    raise ValueError("skill file has unclosed frontmatter")


def _validate_skill_file(path: Path) -> None:
    text = _read_text(path)
    frontmatter = _parse_frontmatter(text)
    if not frontmatter.get("name"):
        raise ValueError(f"skill frontmatter missing name: {path}")
    if not frontmatter.get("description"):
        raise ValueError(f"skill frontmatter missing description: {path}")


def _validate_role_file(path: Path) -> None:
    _read_text(path)


def _resolve_path(source_root: Path, relative_path: str) -> Path:
    path = (source_root / relative_path).resolve()
    if not path.exists():
        raise ValueError(f"resource path not found: {path}")
    return path


def find_skill(spec: ResourceSpec, source_root: Path) -> Path:
    if spec.kind != "skill":
        raise ValueError(f"spec kind mismatch for skill lookup: {spec.kind}")

    candidates: list[Path] = []
    if spec.path:
        candidates.append(_resolve_path(source_root, spec.path))
    candidates.extend(
        [
            source_root / "skills" / spec.name / "SKILL.md",
            source_root / spec.name / "SKILL.md",
        ]
    )
    for path in candidates:
        if path.exists() and path.is_file():
            _validate_skill_file(path)
            return path
    raise ValueError(f"skill not found for: {spec.name}")


def find_role(spec: ResourceSpec, source_root: Path) -> Path:
    if spec.kind != "rule":
        raise ValueError(f"spec kind mismatch for role lookup: {spec.kind}")

    candidates: list[Path] = []
    if spec.path:
        candidates.append(_resolve_path(source_root, spec.path))
    candidates.extend(
        [
            source_root / "roles" / f"{spec.name}.md",
            source_root / f"{spec.name}.md",
        ]
    )
    for path in candidates:
        if path.exists() and path.is_file():
            _validate_role_file(path)
            return path
    raise ValueError(f"role not found for: {spec.name}")


def find_resource(spec: ResourceSpec, source_root: Path) -> Path:
    if spec.kind == "skill":
        return find_skill(spec, source_root)
    if spec.kind == "rule":
        return find_role(spec, source_root)
    raise ValueError(f"unsupported resource kind: {spec.kind}")
