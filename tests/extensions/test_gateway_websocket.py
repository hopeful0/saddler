from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from saddler.extensions.gateway.server.websocket import build_websocket_router


class _FakeBridge:
    """Bridge whose recv() waits for a corresponding send() before yielding a reply.

    This mirrors real ACP behavior (agent replies after receiving input) and
    prevents agent_to_ws from racing ahead of ws_to_agent in tests.
    """

    def __init__(self, responses: list[dict]) -> None:
        self.sent: list[dict] = []
        self._responses = list(responses)
        self._send_event: asyncio.Event | None = None
        self.closed = False

    def _event(self) -> asyncio.Event:
        if self._send_event is None:
            self._send_event = asyncio.Event()
        return self._send_event

    async def send(self, payload: dict) -> None:
        self.sent.append(payload)
        self._event().set()

    async def recv(self) -> dict:
        ev = self._event()
        await ev.wait()
        ev.clear()
        if not self._responses:
            raise EOFError
        return self._responses.pop(0)

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
        self.session = _FakeSession(
            "sid-ws",
            _FakeBridge([{"type": "event", "data": "world"}]),
        )
        self.closed: list[str] = []

    async def create_session(self, agent_ref: str) -> _FakeSession:
        _ = agent_ref
        return self.session

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)
        await self.session.close()


def test_websocket_bidirectional_forwarding_and_cleanup() -> None:
    use_case = _FakeUseCase()
    app = FastAPI()
    app.include_router(build_websocket_router(use_case))
    client = TestClient(app)

    with client.websocket_connect("/agents/a1/ws") as ws:
        ws.send_json({"type": "run", "content": "hello"})
        reply = ws.receive_json()
        assert reply == {"type": "event", "data": "world"}

    assert use_case.session.bridge.sent == [{"type": "run", "content": "hello"}]
    assert "sid-ws" in use_case.closed
