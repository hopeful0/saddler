from __future__ import annotations

import pytest

from saddler.hitch.errors import HitchValidationError
from saddler.hitch.loader import validate_dag, _deep_merge
from saddler.hitch.model import (
    HitchAgentDef,
    HitchBindMount,
    HitchConfig,
    HitchRuntimeDef,
    HitchStorageDef,
    HitchStorageMount,
)
from saddler.hitch.plan import (
    CreateAgentOp,
    CreateRuntimeOp,
    CreateStorageOp,
    StartRuntimeOp,
    build_plan,
)


# ---------------------------------------------------------------------------
# Topological ordering
# ---------------------------------------------------------------------------


def test_storage_before_runtime_before_agent():
    config = HitchConfig(
        storages={"ws": HitchStorageDef(type="local", path="/tmp/ws")},
        runtimes={"rt": HitchRuntimeDef(backend="local", depends_on=["ws"])},
        agents={
            "ag": HitchAgentDef(
                harness="opencode", workdir="/w", runtime="rt", depends_on=["rt"]
            )
        },
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    types = [type(op).__name__ for op in plan.ops]
    assert types == [
        "CreateStorageOp",
        "CreateRuntimeOp",
        "StartRuntimeOp",
        "CreateAgentOp",
    ]


def test_create_then_start_runtime_are_consecutive():
    config = HitchConfig(
        runtimes={
            "rt": HitchRuntimeDef(backend="docker", backend_spec={"image": "img"})
        },
        agents={"ag": HitchAgentDef(harness="claude-code", workdir="/w", runtime="rt")},
    )
    plan = build_plan(config, "/proj/hitch.yaml", "proj")
    ops = plan.ops
    create_idx = next(i for i, o in enumerate(ops) if isinstance(o, CreateRuntimeOp))
    start_idx = next(i for i, o in enumerate(ops) if isinstance(o, StartRuntimeOp))
    assert start_idx == create_idx + 1


def test_plan_carries_project_and_compose_file():
    config = HitchConfig(
        runtimes={"rt": HitchRuntimeDef(backend="local")},
    )
    plan = build_plan(config, "/foo/bar.yaml", "myproject")
    assert plan.project == "myproject"
    assert plan.compose_file == "/foo/bar.yaml"
    for op in plan.ops:
        if isinstance(op, (CreateRuntimeOp, CreateStorageOp, CreateAgentOp)):
            assert op.project == "myproject"
            assert op.compose_file == "/foo/bar.yaml"


def test_multiple_independent_agents_both_appear():
    config = HitchConfig(
        runtimes={"rt": HitchRuntimeDef(backend="local")},
        agents={
            "a1": HitchAgentDef(harness="opencode", workdir="/w1", runtime="rt"),
            "a2": HitchAgentDef(harness="opencode", workdir="/w2", runtime="rt"),
        },
    )
    plan = build_plan(config, "/f.yaml", "p")
    agent_ops = [op for op in plan.ops if isinstance(op, CreateAgentOp)]
    assert {op.service_id for op in agent_ops} == {"a1", "a2"}


# ---------------------------------------------------------------------------
# runtime_is_external
# ---------------------------------------------------------------------------


def test_internal_runtime_ref_not_external():
    config = HitchConfig(
        runtimes={"rt": HitchRuntimeDef(backend="local")},
        agents={"ag": HitchAgentDef(harness="opencode", workdir="/w", runtime="rt")},
    )
    plan = build_plan(config, "/f.yaml", "p")
    agent_op = next(op for op in plan.ops if isinstance(op, CreateAgentOp))
    assert agent_op.runtime_is_external is False


def test_external_runtime_ref_is_external():
    config = HitchConfig(
        agents={
            "ag": HitchAgentDef(
                harness="opencode", workdir="/w", runtime="some-external-id"
            )
        }
    )
    plan = build_plan(config, "/f.yaml", "p")
    agent_op = next(op for op in plan.ops if isinstance(op, CreateAgentOp))
    assert agent_op.runtime_is_external is True


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_cycle_in_agents_raises():
    config = HitchConfig(
        agents={
            "a": HitchAgentDef(
                harness="opencode", workdir="/w", runtime="ext", depends_on=["b"]
            ),
            "b": HitchAgentDef(
                harness="opencode", workdir="/w", runtime="ext", depends_on=["a"]
            ),
        },
    )
    with pytest.raises(HitchValidationError, match="Circular"):
        build_plan(config, "/f.yaml", "p")


def test_cycle_across_types_raises():
    config = HitchConfig(
        storages={"s": HitchStorageDef(type="local", depends_on=["rt"])},
        runtimes={"rt": HitchRuntimeDef(backend="local", depends_on=["s"])},
    )
    with pytest.raises(HitchValidationError):
        validate_dag(config)


def test_unknown_depends_on_raises():
    config = HitchConfig(
        agents={
            "ag": HitchAgentDef(
                harness="opencode",
                workdir="/w",
                runtime="ext",
                depends_on=["nonexistent"],
            )
        }
    )
    with pytest.raises(HitchValidationError, match="nonexistent"):
        validate_dag(config)


def test_duplicate_id_across_types_raises():
    config = HitchConfig(
        storages={"shared": HitchStorageDef(type="local")},
        runtimes={"shared": HitchRuntimeDef(backend="local")},
    )
    with pytest.raises(HitchValidationError, match="Duplicate"):
        validate_dag(config)


# ---------------------------------------------------------------------------
# Mount model
# ---------------------------------------------------------------------------


def test_bind_mount_parsed():
    m = HitchBindMount(type="bind", source=".", destination="/workspace")
    assert m.source == "."
    assert m.destination == "/workspace"
    assert m.mode == "rw"


def test_storage_mount_parsed():
    m = HitchStorageMount(type="storage", storage="ws", destination="/data")
    assert m.storage == "ws"


def test_mount_string_auto_parses_to_bind():
    cfg = HitchConfig.model_validate(
        {
            "runtimes": {
                "rt": {
                    "backend": "local",
                    "mounts": ["./workspace:/workspace:ro"],
                }
            }
        }
    )
    m = cfg.runtimes["rt"].mounts[0]
    assert isinstance(m, HitchBindMount)
    assert m.source == "./workspace"
    assert m.destination == "/workspace"
    assert m.mode == "ro"


def test_mount_string_auto_parses_to_storage():
    cfg = HitchConfig.model_validate(
        {
            "storages": {"ws": {"type": "local"}},
            "runtimes": {
                "rt": {
                    "backend": "local",
                    "mounts": ["ws:/data"],
                }
            },
        }
    )
    m = cfg.runtimes["rt"].mounts[0]
    assert isinstance(m, HitchStorageMount)
    assert m.storage == "ws"
    assert m.destination == "/data"
    assert m.mode == "rw"


# ---------------------------------------------------------------------------
# Deep merge (multi -f)
# ---------------------------------------------------------------------------


def test_deep_merge_adds_new_keys():
    base = {"runtimes": {"rt1": {"backend": "local"}}}
    override = {"runtimes": {"rt2": {"backend": "docker"}}}
    _deep_merge(base, override)
    assert "rt1" in base["runtimes"]
    assert "rt2" in base["runtimes"]


def test_deep_merge_override_wins_for_scalar():
    base = {"name": "old"}
    override = {"name": "new"}
    _deep_merge(base, override)
    assert base["name"] == "new"


def test_deep_merge_nested_dicts_merged():
    base = {"agents": {"a": {"harness": "opencode", "workdir": "/old"}}}
    override = {"agents": {"a": {"workdir": "/new"}}}
    _deep_merge(base, override)
    assert base["agents"]["a"]["harness"] == "opencode"
    assert base["agents"]["a"]["workdir"] == "/new"
