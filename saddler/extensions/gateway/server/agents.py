from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ....api.agent import AgentCreateRequest, ResourceCreateSpec
from ....app.errors import AppError, NotFoundError, ValidationError
from ..api.service import GatewayApiService


class ResourceInput(BaseModel):
    name: str | None = None
    source: str
    path: str | None = None


class AgentCreateInput(BaseModel):
    runtime_ref: str
    harness: str
    workdir: str
    role: ResourceInput | None = None
    skills: list[ResourceInput] = Field(default_factory=list)
    rules: list[ResourceInput] = Field(default_factory=list)
    harness_spec: dict | None = None
    name: str | None = None
    metadata: dict[str, str] | None = None


def _to_resource_spec(resource: ResourceInput) -> ResourceCreateSpec:
    return ResourceCreateSpec(
        name=resource.name or "resource",
        source=resource.source,
        path=resource.path,
    )


def _to_create_request(payload: AgentCreateInput) -> AgentCreateRequest:
    role = None
    if payload.role is not None:
        role = ResourceCreateSpec(
            name="role",
            source=payload.role.source,
            path=payload.role.path,
        )
    return AgentCreateRequest(
        runtime_ref=payload.runtime_ref,
        harness=payload.harness,
        workdir=payload.workdir,
        role=role,
        skills=[_to_resource_spec(item) for item in payload.skills],
        rules=[_to_resource_spec(item) for item in payload.rules],
        harness_spec=payload.harness_spec,
        name=payload.name,
        metadata=payload.metadata,
    )


def _raise_http(exc: AppError) -> None:
    if isinstance(exc, (NotFoundError,)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def build_agents_router(gateway_api: GatewayApiService) -> APIRouter:
    router = APIRouter()

    @router.post("/agents", status_code=201)
    async def create_agent(payload: AgentCreateInput) -> dict:
        try:
            agent = gateway_api.create_agent(_to_create_request(payload))
        except AppError as exc:
            _raise_http(exc)
        return agent.model_dump(mode="json")

    @router.get("/agents")
    async def list_agents() -> list[dict]:
        agents = gateway_api.list_agents()
        return [agent.model_dump(mode="json") for agent in agents]

    @router.get("/agents/{agent_id}")
    async def inspect_agent(agent_id: str) -> dict:
        try:
            agent = gateway_api.inspect_agent(agent_id)
        except AppError as exc:
            _raise_http(exc)
        return agent.model_dump(mode="json")

    @router.delete("/agents/{agent_id}", status_code=204)
    async def remove_agent(agent_id: str) -> None:
        try:
            gateway_api.remove_agent(agent_id)
        except AppError as exc:
            _raise_http(exc)

    return router
