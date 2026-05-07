from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from saddler.app.errors import NotFoundError
from saddler.extensions.gateway.server.auth import AuthMiddleware
from saddler.extensions.gateway.server.pty import build_pty_router


class _FakePtyBridge:
    _EOF = object()

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue()
        self.closed = False

    async def read(self) -> bytes:
        item = await self._queue.get()
        if item is self._EOF:
            raise EOFError
        return item  # type: ignore[return-value]

    def put_output(self, data: bytes) -> None:
        self._queue.put_nowait(data)

    def signal_eof(self) -> None:
        self._queue.put_nowait(self._EOF)

    async def write(self, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.resizes.append((rows, cols))

    async def close(self) -> None:
        self.closed = True


class _FakeTtySession:
    def __init__(self, session_id: str, bridge: _FakePtyBridge) -> None:
        self.session_id = session_id
        self.bridge = bridge


class _FakeUseCase:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.bridge = _FakePtyBridge()
        self.session = _FakeTtySession("sid-tty", self.bridge)
        self.closed: list[str] = []

    async def create_tty_session(self, agent_ref: str) -> _FakeTtySession:
        _ = agent_ref
        if self.fail:
            raise NotFoundError("nope")
        return self.session

    async def close_tty_session(self, session_id: str) -> None:
        self.closed.append(session_id)
        await self.session.bridge.close()


def test_tty_ws_create_failure_closes_with_4404() -> None:
    use_case = _FakeUseCase(fail=True)
    app = FastAPI()
    app.state.gateway_token = "t"
    app.add_middleware(AuthMiddleware)
    app.include_router(build_pty_router(use_case))
    client = TestClient(app)

    with client.websocket_connect(
        "/agents/a1/tty",
        headers={"Authorization": "Bearer t"},
    ) as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
        assert exc.value.code == 4404


def test_tty_ws_binary_and_resize_forwarding() -> None:
    use_case = _FakeUseCase()
    app = FastAPI()
    app.state.gateway_token = "t"
    app.add_middleware(AuthMiddleware)
    app.include_router(build_pty_router(use_case))
    client = TestClient(app)

    with client.websocket_connect(
        "/agents/a1/tty",
        headers={"Authorization": "Bearer t"},
    ) as ws:
        use_case.bridge.put_output(b"out-chunk")
        raw = ws.receive_bytes()
        assert raw == b"out-chunk"
        ws.send_bytes(b"in-chunk")
        ws.send_text(
            json.dumps({"type": "resize", "rows": 30, "cols": 100}),
        )
        use_case.bridge.signal_eof()

    assert use_case.bridge.writes == [b"in-chunk"]
    assert use_case.bridge.resizes == [(30, 100)]
    assert "sid-tty" in use_case.closed
