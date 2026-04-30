from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ....api.agent import AgentApiService
from ....api.runtime import RuntimeApiService
from ....app import build_use_cases
from ..api.service import GatewayApiService
from ..app.gateway import GatewayUseCase
from .auth import AuthMiddleware, build_auth_router
from .streamable_http import build_streamable_http_router
from .ui import mount_gateway_ui
from .websocket import build_websocket_router


def create_gateway_app(token: str) -> FastAPI:
    use_cases = build_use_cases()
    gateway_api = GatewayApiService(
        AgentApiService(use_cases.agent),
        RuntimeApiService(use_cases.runtime, use_cases.storage),
    )
    gateway_use_case = GatewayUseCase(gateway_api)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await gateway_use_case.close_all()

    app = FastAPI(title="saddler gateway", lifespan=lifespan)
    app.state.gateway_token = token
    app.add_middleware(AuthMiddleware)
    app.include_router(build_auth_router())
    app.include_router(build_websocket_router(gateway_use_case))
    app.include_router(build_streamable_http_router(gateway_use_case))
    mount_gateway_ui(app)
    return app
