from __future__ import annotations

import graphlib
from pathlib import Path

import yaml

from .errors import HitchValidationError
from .model import HitchConfig

_DEFAULT_FILENAMES = ("hitch.yaml", "hitch.yml", "saddler.yaml")


def find_default_config(cwd: Path) -> Path:
    for name in _DEFAULT_FILENAMES:
        candidate = cwd / name
        if candidate.exists():
            return candidate
    raise HitchValidationError(
        f"No hitch config found in {cwd}. "
        f"Expected one of: {', '.join(_DEFAULT_FILENAMES)}"
    )


def load_config(
    files: list[Path],
    cwd: Path,
) -> tuple[HitchConfig, str, str]:
    """Parse and merge hitch YAML files.

    Returns (config, project_name, compose_file_abs_path).
    """
    if not files:
        files = [find_default_config(cwd)]

    merged: dict = {}
    for f in files:
        data = yaml.safe_load(f.read_text()) or {}
        _deep_merge(merged, data)

    config = HitchConfig.model_validate(merged)

    project_name = config.name or files[0].resolve().parent.name
    compose_file = str(files[0].resolve())

    return config, project_name, compose_file


def validate_dag(config: HitchConfig) -> None:
    """Raise HitchValidationError if depends_on forms a cycle or references unknown ids."""
    _check_no_duplicate_ids(config)

    all_ids: set[str] = set(config.storages) | set(config.runtimes) | set(config.agents)

    deps: dict[str, set[str]] = {}
    for sid, s in config.storages.items():
        deps[sid] = set(s.depends_on)
    for rid, r in config.runtimes.items():
        deps[rid] = set(r.depends_on)
    for aid, a in config.agents.items():
        deps[aid] = set(a.depends_on)

    for node, node_deps in deps.items():
        unknown = node_deps - all_ids
        if unknown:
            raise HitchValidationError(
                f"'{node}' depends_on references unknown id(s): {sorted(unknown)}"
            )

    try:
        sorter = graphlib.TopologicalSorter(deps)
        list(sorter.static_order())
    except graphlib.CycleError as e:
        raise HitchValidationError(f"Circular dependency detected: {e}") from e


def _check_no_duplicate_ids(config: HitchConfig) -> None:
    storage_ids = set(config.storages)
    runtime_ids = set(config.runtimes)
    agent_ids = set(config.agents)

    dup = (
        (storage_ids & runtime_ids)
        | (storage_ids & agent_ids)
        | (runtime_ids & agent_ids)
    )
    if dup:
        raise HitchValidationError(
            f"Duplicate ids across storages/runtimes/agents: {sorted(dup)}"
        )


def _deep_merge(base: dict, override: dict) -> None:
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
