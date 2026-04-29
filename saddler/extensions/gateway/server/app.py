from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ....api.agent import AgentApiService
from ....api.runtime import RuntimeApiService
from ....app import build_use_cases
from ..api.service import GatewayApiService
from ..app.gateway import GatewayUseCase
from .streamable_http import build_streamable_http_router
from .websocket import build_websocket_router


def create_gateway_app() -> FastAPI:
    use_cases = build_use_cases()
    gateway_api = GatewayApiService(
        AgentApiService(use_cases.agent),
        RuntimeApiService(use_cases.runtime, use_cases.storage),
    )
    gateway_use_case = GatewayUseCase(gateway_api)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.gateway_use_case = gateway_use_case
        yield
        await gateway_use_case.close_all()

    app = FastAPI(title="saddler gateway", lifespan=lifespan)
    app.include_router(build_websocket_router(gateway_use_case))
    app.include_router(build_streamable_http_router(gateway_use_case))
    return app
