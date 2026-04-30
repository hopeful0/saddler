from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ....app.errors import AppError
from ..app.gateway import GatewayUseCase


def build_streamable_http_router(use_case: GatewayUseCase) -> APIRouter:
    router = APIRouter()

    @router.get("/sessions/active")
    async def get_active_sessions() -> dict[str, int]:
        return {"active_sessions": use_case.active_session_count()}

    @router.post("/agents/{agent_id}/sessions", status_code=201)
    async def create_session(agent_id: str) -> dict[str, str]:
        try:
            session = await use_case.create_session(agent_id)
        except AppError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"session_id": session.session_id}

    @router.post("/sessions/{session_id}/input", status_code=202)
    async def write_input(session_id: str, payload: dict) -> dict[str, str]:
        session = use_case.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        await session.bridge.send(payload)
        return {"status": "accepted"}

    @router.get("/sessions/{session_id}/stream")
    async def read_stream(session_id: str) -> StreamingResponse:
        session = use_case.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")

        async def event_stream():
            try:
                while True:
                    payload = await session.bridge.recv()
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except EOFError:
                return
            finally:
                await use_case.close_session(session_id)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> None:
        if use_case.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="session not found")
        await use_case.close_session(session_id)

    return router
