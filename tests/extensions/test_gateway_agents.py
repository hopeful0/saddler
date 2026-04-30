from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saddler.agent.model import Agent, AgentSpec, RoleSpec
from saddler.app.errors import NotFoundError, ValidationError
from saddler.extensions.gateway.server.agents import build_agents_router
from saddler.extensions.gateway.server.auth import AuthMiddleware, build_auth_router


def _agent(agent_id: str, runtime: str = "rt-1") -> Agent:
    return Agent(
        id=agent_id,
        name=f"name-{agent_id}",
        runtime=runtime,
        spec=AgentSpec(
            harness="codex",
            workdir="/work",
            role=RoleSpec(name="role", source="local:///role.md", path=None),
            skills=[],
            rules=[],
            harness_spec=None,
        ),
    )


class _FakeGatewayApi:
    def __init__(self) -> None:
        self.items = {"a1": _agent("a1")}
        self.created_payloads: list[object] = []

    def create_agent(self, req):
        if req.runtime_ref == "missing-runtime":
            raise NotFoundError("runtime not found")
        if req.workdir == "bad-workdir":
            raise ValidationError("workdir must be posix absolute")
        self.created_payloads.append(req)
        created = _agent("a2", runtime=req.runtime_ref)
        self.items[created.id] = created
        return created

    def list_agents(self):
        return [self.items[key] for key in sorted(self.items)]

    def inspect_agent(self, agent_ref: str):
        agent = self.items.get(agent_ref)
        if agent is None:
            raise NotFoundError(f"agent {agent_ref!r} not found")
        return agent

    def remove_agent(self, agent_ref: str) -> None:
        if agent_ref not in self.items:
            raise NotFoundError(f"agent {agent_ref!r} not found")
        self.items.pop(agent_ref)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.gateway_token = "gateway-secret"
    app.add_middleware(AuthMiddleware)
    app.include_router(build_auth_router())
    app.include_router(build_agents_router(_FakeGatewayApi()))
    return app


def test_agents_crud_and_payload_mapping() -> None:
    client = TestClient(_build_app())
    headers = {"Authorization": "Bearer gateway-secret"}

    created = client.post(
        "/agents",
        headers=headers,
        json={
            "runtime_ref": "rt-2",
            "harness": "codex",
            "workdir": "/workspace",
            "role": {"source": "local:///role.md"},
            "skills": [{"name": "s1", "source": "local:///skills/s1"}],
            "rules": [{"name": "r1", "source": "local:///rules/r1"}],
            "metadata": {"env": "dev"},
        },
    )
    assert created.status_code == 201
    assert created.json()["id"] == "a2"
    assert created.json()["runtime"] == "rt-2"

    listed = client.get("/agents", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == ["a1", "a2"]

    inspected = client.get("/agents/a2", headers=headers)
    assert inspected.status_code == 200
    assert inspected.json()["id"] == "a2"

    deleted = client.delete("/agents/a2", headers=headers)
    assert deleted.status_code == 204


def test_agents_error_mapping() -> None:
    client = TestClient(_build_app())
    headers = {"Authorization": "Bearer gateway-secret"}

    missing_runtime = client.post(
        "/agents",
        headers=headers,
        json={
            "runtime_ref": "missing-runtime",
            "harness": "codex",
            "workdir": "/workspace",
        },
    )
    assert missing_runtime.status_code == 404

    validation = client.post(
        "/agents",
        headers=headers,
        json={
            "runtime_ref": "rt-1",
            "harness": "codex",
            "workdir": "bad-workdir",
        },
    )
    assert validation.status_code == 422

    missing_inspect = client.get("/agents/missing", headers=headers)
    assert missing_inspect.status_code == 404

    missing_delete = client.delete("/agents/missing", headers=headers)
    assert missing_delete.status_code == 404


def test_agents_requires_gateway_auth() -> None:
    client = TestClient(_build_app())

    unauthorized = client.get("/agents")
    assert unauthorized.status_code == 401
