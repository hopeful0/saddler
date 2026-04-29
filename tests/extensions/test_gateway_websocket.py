from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saddler.extensions.gateway.server.websocket import build_websocket_router


class _FakeBridge:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.messages: asyncio.Queue[dict | None] = asyncio.Queue()
        self.closed = False

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)

    async def recv(self) -> dict:
        item = await self.messages.get()
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
        self.session = _FakeSession("sid-ws", _FakeBridge())
        self.closed: list[str] = []

    async def create_session(self, agent_ref: str) -> _FakeSession:
        _ = agent_ref
        return self.session

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)
        await self.session.close()


def test_websocket_bidirectional_forwarding_and_cleanup() -> None:
    use_case = _FakeUseCase()
    use_case.session.bridge.messages.put_nowait({"type": "event", "data": "world"})
    app = FastAPI()
    app.include_router(build_websocket_router(use_case))
    client = TestClient(app)

    with client.websocket_connect("/agents/a1/ws") as ws:
        ws.send_json({"type": "run", "content": "hello"})
        reply = ws.receive_json()
        assert reply == {"type": "event", "data": "world"}

    assert use_case.session.bridge.sent == [{"type": "run", "content": "hello"}]
    assert "sid-ws" in use_case.closed
