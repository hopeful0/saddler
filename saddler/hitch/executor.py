from __future__ import annotations

import logging
from pathlib import Path

from ..api.agent import AgentApiService, AgentCreateRequest, ResourceCreateSpec
from ..api.runtime import MountSpec, RuntimeApiService, RuntimeCreateRequest
from ..api.storage import StorageApiService, StorageCreateRequest
from ..app import UseCases
from ..runtime.model import Runtime
from ..storage.model import Storage
from ..agent.model import Agent
from .model import HitchBindMount, HitchStorageMount
from .plan import (
    CreateAgentOp,
    CreateRuntimeOp,
    CreateStorageOp,
    HitchPlan,
    StartRuntimeOp,
)

log = logging.getLogger(__name__)

HITCH_PROJECT = "hitch.project"
HITCH_FILE = "hitch.compose_file"
HITCH_SERVICE = "hitch.service"


class HitchExecutor:
    def __init__(self, ucs: UseCases) -> None:
        self._storage_api = StorageApiService(ucs.storage)
        self._runtime_api = RuntimeApiService(ucs.runtime, ucs.storage)
        self._agent_api = AgentApiService(ucs.agent)

    # ------------------------------------------------------------------
    # up
    # ------------------------------------------------------------------

    def up(self, plan: HitchPlan, *, force_recreate: bool = False) -> None:
        compose_dir = Path(plan.compose_file).parent

        if force_recreate:
            self._remove_all_project_resources(plan.project)

        # Maps logical service_id → created record id (for cross-references)
        storage_id_map: dict[str, str] = {}
        runtime_id_map: dict[str, str] = {}

        for op in plan.ops:
            if isinstance(op, CreateStorageOp):
                record = self._create_storage(op, compose_dir)
                storage_id_map[op.service_id] = record.id
                log.info("Created storage '%s' (%s)", op.service_id, record.id)

            elif isinstance(op, CreateRuntimeOp):
                record = self._create_runtime(op, compose_dir, storage_id_map)
                runtime_id_map[op.service_id] = record.id
                log.info("Created runtime '%s' (%s)", op.service_id, record.id)

            elif isinstance(op, StartRuntimeOp):
                rt_id = runtime_id_map[op.service_id]
                self._runtime_api.start(rt_id)
                log.info("Started runtime '%s' (%s)", op.service_id, rt_id)

            elif isinstance(op, CreateAgentOp):
                if op.runtime_is_external:
                    runtime_ref = op.defn.runtime
                else:
                    runtime_ref = runtime_id_map[op.defn.runtime]
                record = self._create_agent(op, runtime_ref)
                log.info("Created agent '%s' (%s)", op.service_id, record.id)

    def _create_storage(self, op: CreateStorageOp, compose_dir: Path) -> Storage:
        defn = op.defn
        path = defn.path
        if path is not None:
            path = str(_resolve_path(path, compose_dir))
        return self._storage_api.create(
            StorageCreateRequest(
                type=defn.type,
                path=path,
                server=defn.server,
                name=_resolve_name(defn.name, op.project, op.service_id),
                metadata=_hitch_meta(op.project, op.compose_file, op.service_id),
            )
        )

    def _create_runtime(
        self,
        op: CreateRuntimeOp,
        compose_dir: Path,
        storage_id_map: dict[str, str],
    ) -> Runtime:
        defn = op.defn
        mounts: list[MountSpec] = []
        for m in defn.mounts:
            if isinstance(m, HitchBindMount):
                mounts.append(
                    MountSpec(
                        type="bind",
                        source=str(_resolve_path(m.source, compose_dir)),
                        destination=m.destination,
                        mode=m.mode,
                    )
                )
            elif isinstance(m, HitchStorageMount):
                storage_record_id = storage_id_map.get(m.storage)
                if storage_record_id is None:
                    raise RuntimeError(
                        f"Runtime '{op.service_id}' mounts storage '{m.storage}' "
                        "which was not created in this hitch run"
                    )
                mounts.append(
                    MountSpec(
                        type="storage",
                        storage_ref=storage_record_id,
                        destination=m.destination,
                        mode=m.mode,
                    )
                )

        return self._runtime_api.create(
            RuntimeCreateRequest(
                backend_type=defn.backend,
                env=defn.env,
                mounts=mounts,
                backend_spec=defn.backend_spec,
                name=_resolve_name(defn.name, op.project, op.service_id),
                metadata=_hitch_meta(op.project, op.compose_file, op.service_id),
            )
        )

    def _create_agent(self, op: CreateAgentOp, runtime_ref: str) -> Agent:
        defn = op.defn
        role: ResourceCreateSpec | None = None
        if defn.role is not None:
            role = ResourceCreateSpec(
                name="role", source=defn.role.source, path=defn.role.path
            )
        skills = [
            ResourceCreateSpec(name=s.name, source=s.source, path=s.path)
            for s in defn.skills
        ]
        rules = [
            ResourceCreateSpec(name=r.name, source=r.source, path=r.path)
            for r in defn.rules
        ]
        return self._agent_api.create(
            AgentCreateRequest(
                runtime_ref=runtime_ref,
                harness=defn.harness,
                workdir=defn.workdir,
                role=role,
                skills=skills,
                rules=rules,
                name=_resolve_name(defn.name, op.project, op.service_id),
                metadata=_hitch_meta(op.project, op.compose_file, op.service_id),
            )
        )

    def _remove_all_project_resources(self, project: str) -> None:
        """Remove every hitch-owned resource for this project (used by --force-recreate)."""
        for agent in self._agent_api.list():
            if (agent.metadata or {}).get(HITCH_PROJECT) == project:
                self._agent_api.remove(agent.id)
        for runtime in self._runtime_api.list():
            if (runtime.metadata or {}).get(HITCH_PROJECT) == project:
                self._runtime_api.remove(runtime.id, force=True)
        for storage in self._storage_api.list():
            if (storage.metadata or {}).get(HITCH_PROJECT) == project:
                self._storage_api.remove(storage.id)

    # ------------------------------------------------------------------
    # stop
    # ------------------------------------------------------------------

    def stop(self, project: str) -> None:
        for runtime in self._runtime_api.list():
            if (runtime.metadata or {}).get(HITCH_PROJECT) == project:
                self._runtime_api.stop(runtime.id)
                log.info("Stopped runtime %s", runtime.id)

    # ------------------------------------------------------------------
    # down
    # ------------------------------------------------------------------

    def down(self, project: str) -> None:
        self.stop(project)

        for agent in self._agent_api.list():
            if (agent.metadata or {}).get(HITCH_PROJECT) == project:
                self._agent_api.remove(agent.id)
                log.info("Removed agent %s", agent.id)

        errors: list[str] = []
        for runtime in self._runtime_api.list():
            if (runtime.metadata or {}).get(HITCH_PROJECT) != project:
                continue
            if runtime.used_by:
                errors.append(
                    f"Runtime {runtime.id!r} still referenced by: {runtime.used_by}"
                )
            else:
                self._runtime_api.remove(runtime.id)
                log.info("Removed runtime %s", runtime.id)

        for storage in self._storage_api.list():
            if (storage.metadata or {}).get(HITCH_PROJECT) != project:
                continue
            if storage.mounted_by:
                errors.append(
                    f"Storage {storage.id!r} still mounted by: {storage.mounted_by}"
                )
            else:
                self._storage_api.remove(storage.id)
                log.info("Removed storage %s", storage.id)

        if errors:
            raise RuntimeError(
                "hitch down incomplete — some resources could not be removed:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

    # ------------------------------------------------------------------
    # ps
    # ------------------------------------------------------------------

    def ps(self, project: str) -> dict[str, list]:
        storages = [
            s
            for s in self._storage_api.list()
            if (s.metadata or {}).get(HITCH_PROJECT) == project
        ]
        runtimes_with_status = [
            (r, status)
            for r, status in self._runtime_api.list_with_status()
            if (r.metadata or {}).get(HITCH_PROJECT) == project
        ]
        agents = [
            a
            for a in self._agent_api.list()
            if (a.metadata or {}).get(HITCH_PROJECT) == project
        ]
        return {
            "storages": storages,
            "runtimes": runtimes_with_status,
            "agents": agents,
        }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _hitch_meta(project: str, compose_file: str, service_id: str) -> dict[str, str]:
    return {
        HITCH_PROJECT: project,
        HITCH_FILE: compose_file,
        HITCH_SERVICE: service_id,
    }


def _resolve_path(path: str, base: Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def _resolve_name(name: str | None, project: str, service_id: str) -> str:
    if name:
        return name
    return f"{project}-{service_id}"
