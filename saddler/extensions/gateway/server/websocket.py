from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ....app.errors import AppError
from ..app.gateway import GatewayUseCase


def build_websocket_router(use_case: GatewayUseCase) -> APIRouter:
    router = APIRouter()

    @router.websocket("/agents/{agent_id}/ws")
    async def agent_ws(websocket: WebSocket, agent_id: str) -> None:
        await websocket.accept()
        try:
            session = await use_case.create_session(agent_id)
        except AppError:
            await websocket.close(code=4404)
            return

        async def ws_to_agent() -> None:
            while True:
                payload = await websocket.receive_json()
                await session.bridge.send(payload)

        async def agent_to_ws() -> None:
            while True:
                payload = await session.bridge.recv()
                await websocket.send_json(payload)

        tasks = [
            asyncio.create_task(ws_to_agent()),
            asyncio.create_task(agent_to_ws()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(Exception):
                    await task
            await use_case.close_session(session.session_id)

    return router
