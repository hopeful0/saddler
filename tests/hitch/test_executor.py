"""Executor tests using in-memory fakes (no real runtime backends)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from saddler.hitch.executor import (
    HITCH_FILE,
    HITCH_PROJECT,
    HITCH_SERVICE,
    HitchExecutor,
)
from saddler.hitch.model import (
    HitchAgentDef,
    HitchBindMount,
    HitchConfig,
    HitchRuntimeDef,
    HitchStorageDef,
    HitchStorageMount,
)
from saddler.hitch.plan import build_plan


# ---------------------------------------------------------------------------
# Fake UseCases + API stubs
# ---------------------------------------------------------------------------


class FakeStorageApi:
    def __init__(self):
        self._store: dict[str, object] = {}
        self.created: list = []
        self.removed: list = []

    def create(self, req):
        rec = SimpleNamespace(
            id=str(uuid4()),
            name=req.name,
            metadata=req.metadata,
            mounted_by=[],
            spec=SimpleNamespace(type=req.type),
        )
        self._store[rec.id] = rec
        self.created.append(req)
        return rec

    def list(self):
        return list(self._store.values())

    def inspect(self, ref):
        return self._store.get(ref)

    def remove(self, ref, *, force=False):
        self.removed.append(ref)
        self._store.pop(ref, None)


class FakeRuntimeApi:
    def __init__(self):
        self._store: dict[str, object] = {}
        self.created: list = []
        self.started: list = []
        self.stopped: list = []
        self.removed: list = []

    def create(self, req):
        rec = SimpleNamespace(
            id=str(uuid4()),
            name=req.name,
            metadata=req.metadata,
            used_by=[],
            spec=SimpleNamespace(backend_type=req.backend_type),
        )
        self._store[rec.id] = rec
        self.created.append(req)
        return rec

    def start(self, ref):
        self.started.append(ref)
        return self._store.get(ref)

    def stop(self, ref):
        self.stopped.append(ref)

    def remove(self, ref, *, force=False):
        self.removed.append(ref)
        self._store.pop(ref, None)

    def list(self):
        return list(self._store.values())

    def list_with_status(self):
        return [(r, "not running") for r in self._store.values()]

    def inspect(self, ref):
        return self._store.get(ref)


class FakeAgentApi:
    def __init__(self):
        self._store: dict[str, object] = {}
        self.created: list = []
        self.removed: list = []

    def create(self, req):
        rec = SimpleNamespace(
            id=str(uuid4()),
            name=req.name,
            metadata=req.metadata,
            spec=SimpleNamespace(harness=req.harness),
        )
        self._store[rec.id] = rec
        self.created.append(req)
        return rec

    def remove(self, ref):
        self.removed.append(ref)
        self._store.pop(ref, None)

    def list(self):
        return list(self._store.values())

    def inspect(self, ref):
        return self._store.get(ref)


class FakeUseCases:
    """Quacks like UseCases; executor builds API services from it internally."""

    pass


def _make_executor() -> tuple[
    HitchExecutor, FakeStorageApi, FakeRuntimeApi, FakeAgentApi
]:
    """Build an executor backed entirely by fakes, bypassing real UseCase wiring."""
    storage_api = FakeStorageApi()
    runtime_api = FakeRuntimeApi()
    agent_api = FakeAgentApi()

    exc = HitchExecutor.__new__(HitchExecutor)
    exc._storage_api = storage_api
    exc._runtime_api = runtime_api
    exc._agent_api = agent_api
    return exc, storage_api, runtime_api, agent_api


# ---------------------------------------------------------------------------
# up — basic creation
# ---------------------------------------------------------------------------


def test_up_creates_runtime_and_agent():
    config = HitchConfig(
        runtimes={"rt": HitchRuntimeDef(backend="local")},
        agents={"ag": HitchAgentDef(harness="opencode", workdir="/w", runtime="rt")},
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc, _, runtime_api, agent_api = _make_executor()
    exc.up(plan)

    assert len(runtime_api.created) == 1
    assert len(runtime_api.started) == 1
    assert len(agent_api.created) == 1


def test_up_storage_uses_type_not_kind():
    config = HitchConfig(
        storages={"ws": HitchStorageDef(type="local", path="/tmp/ws")},
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc, storage_api, _, _ = _make_executor()
    exc.up(plan)

    req = storage_api.created[0]
    assert req.type == "local"


def test_up_creates_storage_with_correct_metadata():
    config = HitchConfig(
        storages={"ws": HitchStorageDef(type="local", path="/tmp/ws")},
    )
    plan = build_plan(config, "/proj/hitch.yaml", "myproject")
    exc, storage_api, _, _ = _make_executor()
    exc.up(plan)

    req = storage_api.created[0]
    assert req.metadata[HITCH_PROJECT] == "myproject"
    assert req.metadata[HITCH_SERVICE] == "ws"
    assert req.metadata[HITCH_FILE] == "/proj/hitch.yaml"


def test_up_runtime_uses_backend_field():
    config = HitchConfig(
        runtimes={
            "rt": HitchRuntimeDef(backend="docker", backend_spec={"image": "myimage"})
        },
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc, _, runtime_api, _ = _make_executor()
    exc.up(plan)

    req = runtime_api.created[0]
    assert req.backend_type == "docker"
    assert req.backend_spec == {"image": "myimage"}


def test_up_agent_receives_correct_runtime_id():
    """Agent should be created with the record id, not the logical service name."""
    config = HitchConfig(
        runtimes={"rt": HitchRuntimeDef(backend="local")},
        agents={"ag": HitchAgentDef(harness="opencode", workdir="/w", runtime="rt")},
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc, _, runtime_api, agent_api = _make_executor()
    exc.up(plan)

    runtime_record_id = list(runtime_api._store.values())[0].id
    assert agent_api.created[0].runtime_ref == runtime_record_id


def test_up_external_runtime_ref_passed_through():
    config = HitchConfig(
        agents={
            "ag": HitchAgentDef(
                harness="opencode", workdir="/w", runtime="external-rt-id"
            )
        }
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc, _, _, agent_api = _make_executor()
    exc.up(plan)

    assert agent_api.created[0].runtime_ref == "external-rt-id"


def test_up_bind_mount_resolved_relative_to_compose_dir(tmp_path):
    (tmp_path / "ws").mkdir()
    compose_file = str(tmp_path / "hitch.yaml")
    config = HitchConfig(
        runtimes={
            "rt": HitchRuntimeDef(
                backend="local",
                mounts=[
                    HitchBindMount(type="bind", source="ws", destination="/workspace")
                ],
            )
        },
    )
    plan = build_plan(config, compose_file, "proj")
    exc, _, runtime_api, _ = _make_executor()
    exc.up(plan)

    req = runtime_api.created[0]
    mount = req.mounts[0]
    assert mount.type == "bind"
    assert mount.source == str(tmp_path / "ws")


def test_up_storage_mount_resolves_to_record_id():
    config = HitchConfig(
        storages={"ws": HitchStorageDef(type="local", path="/tmp/ws")},
        runtimes={
            "rt": HitchRuntimeDef(
                backend="local",
                mounts=[
                    HitchStorageMount(type="storage", storage="ws", destination="/data")
                ],
                depends_on=["ws"],
            )
        },
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc, storage_api, runtime_api, _ = _make_executor()
    exc.up(plan)

    storage_record_id = list(storage_api._store.values())[0].id
    req = runtime_api.created[0]
    mount = req.mounts[0]
    assert mount.type == "storage"
    assert mount.storage_ref == storage_record_id


def test_up_backend_spec_passthrough():
    spec = {"image": "myimg", "user": "1000:1000", "command": ["sleep", "infinity"]}
    config = HitchConfig(
        runtimes={"rt": HitchRuntimeDef(backend="docker", backend_spec=spec)},
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc, _, runtime_api, _ = _make_executor()
    exc.up(plan)

    assert runtime_api.created[0].backend_spec == spec


def test_up_defaults_names_to_project_service_id():
    config = HitchConfig(
        storages={"ws": HitchStorageDef(type="local", path="/tmp/ws")},
        runtimes={
            "rt": HitchRuntimeDef(backend="local", depends_on=["ws"]),
        },
        agents={
            "ag": HitchAgentDef(
                harness="opencode",
                workdir="/w",
                runtime="rt",
                depends_on=["rt"],
            )
        },
    )
    plan = build_plan(config, "/proj/hitch.yaml", "myproj")
    exc, storage_api, runtime_api, agent_api = _make_executor()
    exc.up(plan)

    assert storage_api.created[0].name == "myproj-ws"
    assert runtime_api.created[0].name == "myproj-rt"
    assert agent_api.created[0].name == "myproj-ag"


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_stops_only_project_runtimes():
    exc, _, runtime_api, _ = _make_executor()
    rt_a = SimpleNamespace(
        id="rt-a",
        metadata={HITCH_PROJECT: "projA"},
        used_by=[],
        spec=SimpleNamespace(backend_type="local"),
    )
    rt_b = SimpleNamespace(
        id="rt-b",
        metadata={HITCH_PROJECT: "projB"},
        used_by=[],
        spec=SimpleNamespace(backend_type="local"),
    )
    runtime_api._store["rt-a"] = rt_a
    runtime_api._store["rt-b"] = rt_b

    exc.stop("projA")

    assert "rt-a" in runtime_api.stopped
    assert "rt-b" not in runtime_api.stopped


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------


def test_down_removes_agent_and_runtime_for_project():
    config = HitchConfig(
        runtimes={"rt": HitchRuntimeDef(backend="local")},
        agents={"ag": HitchAgentDef(harness="opencode", workdir="/w", runtime="rt")},
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc, _, runtime_api, agent_api = _make_executor()
    exc.up(plan)

    runtime_api.stopped.clear()
    runtime_api.removed.clear()
    agent_api.removed.clear()

    exc.down("proj")

    assert len(agent_api.removed) == 1
    assert len(runtime_api.removed) == 1


def test_down_does_not_remove_external_agent():
    exc, _, _, agent_api = _make_executor()
    external = SimpleNamespace(
        id="ext-agent", metadata={}, spec=SimpleNamespace(harness="opencode")
    )
    agent_api._store["ext-agent"] = external

    exc.down("proj")

    assert "ext-agent" not in agent_api.removed


def test_down_reports_error_when_runtime_still_used():
    exc, _, runtime_api, _ = _make_executor()
    rt = SimpleNamespace(
        id="rt-id",
        metadata={HITCH_PROJECT: "proj"},
        used_by=["some-agent"],
        spec=SimpleNamespace(backend_type="local"),
    )
    runtime_api._store["rt-id"] = rt

    with pytest.raises(RuntimeError, match="could not be removed"):
        exc.down("proj")


# ---------------------------------------------------------------------------
# ps — uses list_with_status
# ---------------------------------------------------------------------------


def test_ps_filters_by_project_and_returns_status():
    exc, _, runtime_api, agent_api = _make_executor()
    rt_owned = SimpleNamespace(
        id="rt-1",
        metadata={HITCH_PROJECT: "proj"},
        used_by=[],
        spec=SimpleNamespace(backend_type="local"),
    )
    rt_other = SimpleNamespace(
        id="rt-2",
        metadata={HITCH_PROJECT: "other"},
        used_by=[],
        spec=SimpleNamespace(backend_type="local"),
    )
    runtime_api._store["rt-1"] = rt_owned
    runtime_api._store["rt-2"] = rt_other

    result = exc.ps("proj")
    assert len(result["runtimes"]) == 1
    rt, status = result["runtimes"][0]
    assert rt.id == "rt-1"
    assert status == "not running"


def test_ps_agents_filtered_by_project():
    exc, _, _, agent_api = _make_executor()
    agent_api._store["ag-1"] = SimpleNamespace(
        id="ag-1",
        metadata={HITCH_PROJECT: "proj"},
        spec=SimpleNamespace(harness="opencode"),
    )
    agent_api._store["ag-2"] = SimpleNamespace(
        id="ag-2",
        metadata={HITCH_PROJECT: "other"},
        spec=SimpleNamespace(harness="opencode"),
    )

    result = exc.ps("proj")
    assert len(result["agents"]) == 1
    assert result["agents"][0].id == "ag-1"


# ---------------------------------------------------------------------------
# force_recreate — removes ALL project resources
# ---------------------------------------------------------------------------


def test_force_recreate_removes_all_project_resources_not_just_current_config():
    """Even resources from a previous config that are no longer declared get removed."""
    exc, _, runtime_api, agent_api = _make_executor()

    # Pre-populate "old" resources with hitch metadata for the same project
    old_rt = SimpleNamespace(
        id="old-rt",
        metadata={HITCH_PROJECT: "proj"},
        used_by=[],
        spec=SimpleNamespace(backend_type="local"),
    )
    old_ag = SimpleNamespace(
        id="old-ag",
        metadata={HITCH_PROJECT: "proj"},
        spec=SimpleNamespace(harness="opencode"),
    )
    runtime_api._store["old-rt"] = old_rt
    agent_api._store["old-ag"] = old_ag

    # New config declares different services
    config = HitchConfig(
        runtimes={"new-rt": HitchRuntimeDef(backend="local")},
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    exc.up(plan, force_recreate=True)

    # Old resources should be gone
    assert "old-rt" in runtime_api.removed
    assert "old-ag" in agent_api.removed
    # New runtime created
    assert len(runtime_api.created) == 1
