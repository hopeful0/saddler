from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saddler.extensions.gateway.server.streamable_http import (
    build_streamable_http_router,
)


class _FakeBridge:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._responses: list[dict | None] = []
        self._queue: asyncio.Queue | None = None
        self.closed = False

    def _get_queue(self) -> asyncio.Queue:
        if self._queue is None:
            self._queue = asyncio.Queue()
            for item in self._responses:
                self._queue.put_nowait(item)
        return self._queue

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)

    async def recv(self) -> dict:
        item = await self._get_queue().get()
        if item is None:
            raise EOFError
        return item

    async def close(self) -> None:
        self.closed = True


class _FakeSession:
    def __init__(self, session_id: str, bridge: _FakeBridge) -> None:
        self.session_id = session_id
        self.bridge = bridge

    async def close(self) -> None:
        await self.bridge.close()


class _FakeUseCase:
    def __init__(self) -> None:
        self.sessions: dict[str, _FakeSession] = {}
        self.closed: list[str] = []

    async def create_session(self, agent_ref: str) -> _FakeSession:
        _ = agent_ref
        sid = "sid-1"
        session = _FakeSession(sid, _FakeBridge())
        self.sessions[sid] = session
        return session

    def get_session(self, session_id: str) -> _FakeSession | None:
        return self.sessions.get(session_id)

    async def close_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        self.closed.append(session_id)
        await session.close()


def test_streamable_http_session_lifecycle() -> None:
    use_case = _FakeUseCase()
    app = FastAPI()
    app.include_router(build_streamable_http_router(use_case))
    client = TestClient(app)

    created = client.post("/agents/a1/sessions")
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    accepted = client.post(
        f"/sessions/{session_id}/input",
        json={"type": "run", "content": "hello"},
    )
    assert accepted.status_code == 202
    assert use_case.sessions[session_id].bridge.sent == [
        {"type": "run", "content": "hello"}
    ]

    use_case.sessions[session_id].bridge._responses.extend(
        [{"type": "event", "data": "world"}, None]
    )
    stream = client.get(f"/sessions/{session_id}/stream")
    assert stream.status_code == 200
    assert 'data: {"type": "event", "data": "world"}' in stream.text

    missing_delete = client.delete("/sessions/bad-sid")
    assert missing_delete.status_code == 404
