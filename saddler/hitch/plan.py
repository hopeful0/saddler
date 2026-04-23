from __future__ import annotations

import graphlib
from dataclasses import dataclass

from .errors import HitchValidationError
from .model import HitchAgentDef, HitchConfig, HitchRuntimeDef, HitchStorageDef


@dataclass(frozen=True)
class CreateStorageOp:
    service_id: str
    defn: HitchStorageDef
    project: str
    compose_file: str


@dataclass(frozen=True)
class CreateRuntimeOp:
    service_id: str
    defn: HitchRuntimeDef
    project: str
    compose_file: str


@dataclass(frozen=True)
class StartRuntimeOp:
    service_id: str


@dataclass(frozen=True)
class CreateAgentOp:
    service_id: str
    defn: HitchAgentDef
    project: str
    compose_file: str
    runtime_is_external: bool  # True when defn.runtime is not a key in config.runtimes


HitchOp = CreateStorageOp | CreateRuntimeOp | StartRuntimeOp | CreateAgentOp


@dataclass(frozen=True)
class HitchPlan:
    project: str
    compose_file: str
    ops: tuple[HitchOp, ...]


def build_plan(config: HitchConfig, compose_file: str, project: str) -> HitchPlan:
    """Build a topologically-sorted operation sequence from a validated HitchConfig."""
    deps: dict[str, set[str]] = {}
    for sid in config.storages:
        deps[sid] = set(config.storages[sid].depends_on)
    for rid in config.runtimes:
        deps[rid] = set(config.runtimes[rid].depends_on)
    for aid in config.agents:
        deps[aid] = set(config.agents[aid].depends_on)

    try:
        order = list(graphlib.TopologicalSorter(deps).static_order())
    except graphlib.CycleError as e:
        raise HitchValidationError(f"Circular dependency detected: {e}") from e

    ops: list[HitchOp] = []
    for node_id in order:
        if node_id in config.storages:
            ops.append(
                CreateStorageOp(
                    service_id=node_id,
                    defn=config.storages[node_id],
                    project=project,
                    compose_file=compose_file,
                )
            )
        elif node_id in config.runtimes:
            ops.append(
                CreateRuntimeOp(
                    service_id=node_id,
                    defn=config.runtimes[node_id],
                    project=project,
                    compose_file=compose_file,
                )
            )
            ops.append(StartRuntimeOp(service_id=node_id))
        elif node_id in config.agents:
            defn = config.agents[node_id]
            ops.append(
                CreateAgentOp(
                    service_id=node_id,
                    defn=defn,
                    project=project,
                    compose_file=compose_file,
                    runtime_is_external=defn.runtime not in config.runtimes,
                )
            )

    return HitchPlan(project=project, compose_file=compose_file, ops=tuple(ops))
