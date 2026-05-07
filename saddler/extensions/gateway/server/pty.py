from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect

from ....app.errors import AppError
from ..app.gateway import GatewayUseCase


def build_pty_router(use_case: GatewayUseCase) -> APIRouter:
    router = APIRouter()

    @router.websocket("/agents/{agent_id}/tty")
    async def agent_tty(websocket: WebSocket, agent_id: str) -> None:
        await websocket.accept()
        try:
            session = await use_case.create_tty_session(agent_id)
        except AppError:
            await websocket.close(code=4404)
            return

        bridge = session.bridge

        async def pty_to_ws() -> None:
            try:
                while True:
                    chunk = await bridge.read()
                    await websocket.send_bytes(chunk)
            except (EOFError, WebSocketDisconnect):
                return

        async def ws_to_pty() -> None:
            try:
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    if msg.get("bytes") is not None:
                        await bridge.write(msg["bytes"])
                    elif msg.get("text") is not None:
                        body = json.loads(msg["text"])
                        if body.get("type") == "resize":
                            await bridge.resize(
                                int(body["rows"]),
                                int(body["cols"]),
                            )
            except WebSocketDisconnect:
                return

        tasks = [
            asyncio.create_task(pty_to_ws()),
            asyncio.create_task(ws_to_pty()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await use_case.close_tty_session(session.session_id)

    return router
