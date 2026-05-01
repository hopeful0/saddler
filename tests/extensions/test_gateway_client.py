from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

pytest.importorskip("websockets")
pytest.importorskip("httpx")

from saddler.extensions.gateway.client import (
    GatewayClient,
    HttpRemoteSession,
    WsRemoteSession,
)
from saddler.extensions.gateway.client import ws as ws_mod


class FakeWebSocket:
    def __init__(self, recv_values: list[str | bytes]) -> None:
        self.sent: list[str] = []
        self._recv_q: asyncio.Queue[str | bytes] = asyncio.Queue()
        for v in recv_values:
            self._recv_q.put_nowait(v)
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str | bytes:
        return await self._recv_q.get()

    async def close(self) -> None:
        self.closed = True


def test_gateway_agent_ws_uri_schemes() -> None:
    assert (
        ws_mod.gateway_agent_ws_uri("http://host:9/x", "ag1")
        == "ws://host:9/x/agents/ag1/ws"
    )
    assert ws_mod.gateway_agent_ws_uri("https://h/", "a") == "wss://h/agents/a/ws"
    assert ws_mod.gateway_agent_ws_uri("ws://h", "b") == "ws://h/agents/b/ws"
    assert ws_mod.gateway_agent_ws_uri("wss://h", "b") == "wss://h/agents/b/ws"


@pytest.mark.anyio
async def test_ws_remote_session_send_recv_close() -> None:
    fake = FakeWebSocket([json.dumps({"type": "event", "data": "world"})])
    session = WsRemoteSession(fake)
    await session.send({"type": "run", "content": "hello"})
    assert json.loads(fake.sent[0]) == {"type": "run", "content": "hello"}
    msg = await session.recv()
    assert msg == {"type": "event", "data": "world"}
    await session.close()
    assert fake.closed is True
    await session.close()
    assert fake.closed is True


@pytest.mark.anyio
async def test_ws_remote_session_connect_uses_scheme_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_connect(uri: str, **kwargs: Any) -> FakeWebSocket:
        captured["uri"] = uri
        captured["headers"] = kwargs.get("additional_headers")
        return FakeWebSocket([])

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    session = await WsRemoteSession.connect(
        "http://127.0.0.1:1", "my-agent", token="tok"
    )
    assert captured["uri"] == "ws://127.0.0.1:1/agents/my-agent/ws"
    assert captured["headers"] == [("Authorization", "Bearer tok")]
    await session.close()


@pytest.mark.anyio
async def test_http_remote_session_connect_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sse_body = 'data: {"x": 1}\n\ndata: {"x": 2}\n\n'
    captured_timeout: httpx.Timeout | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/agents/a/sessions":
            return httpx.Response(201, json={"session_id": "s1"})
        if request.method == "POST" and path == "/sessions/s1/input":
            assert json.loads(request.content.decode()) == {"type": "run"}
            return httpx.Response(202, json={"status": "accepted"})
        if request.method == "GET" and path == "/sessions/s1/stream":
            return httpx.Response(
                200, text=sse_body, headers={"content-type": "text/event-stream"}
            )
        if request.method == "DELETE" and path == "/sessions/s1":
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _make_client(**kwargs: Any) -> httpx.AsyncClient:
        nonlocal captured_timeout
        captured_timeout = kwargs.get("timeout")
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(
        "saddler.extensions.gateway.client.http.httpx.AsyncClient",
        _make_client,
    )
    session = await HttpRemoteSession.connect("http://gateway.test", "a", token="tok")
    assert captured_timeout is not None
    assert captured_timeout.read is None
    await session.send({"type": "run"})
    assert await session.recv() == {"x": 1}
    assert await session.recv() == {"x": 2}
    await session.close()


@pytest.mark.anyio
async def test_gateway_client_dispatches_and_context_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []

    class FakeWs:
        def __init__(self) -> None:
            created.append("ws")

        async def send(self, msg: dict) -> None:
            _ = msg

        async def recv(self) -> dict:
            raise EOFError

        async def close(self) -> None:
            created.append("ws-close")

    class FakeHttp:
        def __init__(self) -> None:
            created.append("http")

        async def send(self, msg: dict) -> None:
            _ = msg

        async def recv(self) -> dict:
            raise EOFError

        async def close(self) -> None:
            created.append("http-close")

    async def fake_ws_connect(*a: Any, **k: Any) -> FakeWs:
        return FakeWs()

    async def fake_http_connect(
        cls: type,
        url: str,
        agent_ref: str,
        token: str,
    ) -> FakeHttp:
        _ = (cls, url, agent_ref, token)
        return FakeHttp()

    monkeypatch.setattr(WsRemoteSession, "connect", staticmethod(fake_ws_connect))
    monkeypatch.setattr(HttpRemoteSession, "connect", classmethod(fake_http_connect))

    async with GatewayClient("http://h", "t") as gc:
        ws_sess = await gc.connect("ag", transport="ws")
        assert isinstance(ws_sess, FakeWs)
        http_sess = await gc.connect("ag", transport="http")
        assert isinstance(http_sess, FakeHttp)

    assert created == ["ws", "http", "ws-close", "http-close"]


def test_connect_cli_uses_env_token_and_prints_ndjson(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from typer.testing import CliRunner

    from saddler.extensions.gateway import cli

    monkeypatch.setenv("SADDLER_GATEWAY_TOKEN", "env-tok")

    class FakeSession:
        def __init__(self) -> None:
            self.closed = False
            self._n = 0

        async def send(self, msg: dict) -> None:
            assert msg == {"k": 1}

        async def recv(self) -> dict:
            self._n += 1
            if self._n == 1:
                return {"out": True}
            raise EOFError

        async def close(self) -> None:
            self.closed = True

    instances: list[Any] = []

    class FakeGatewayClient:
        last_session: FakeSession | None = None

        def __init__(self, url: str, token: str) -> None:
            self.url = url
            self.token = token
            instances.append(self)

        async def connect(self, agent_ref: str, transport: str = "ws") -> FakeSession:
            assert agent_ref == "agent-1"
            assert transport == "ws"
            FakeGatewayClient.last_session = FakeSession()
            return FakeGatewayClient.last_session

    monkeypatch.setattr(
        "saddler.extensions.gateway.client.GatewayClient",
        FakeGatewayClient,
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.gateway_app,
        ["connect", "http://localhost:9", "agent-1"],
        input=json.dumps({"k": 1}) + "\n",
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert instances[0].token == "env-tok"
    assert FakeGatewayClient.last_session is not None
    assert FakeGatewayClient.last_session.closed is True
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert any(json.loads(ln) == {"out": True} for ln in lines)


def test_connect_cli_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from saddler.extensions.gateway import cli

    monkeypatch.delenv("SADDLER_GATEWAY_TOKEN", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli.gateway_app, ["connect", "http://x", "a"])
    assert result.exit_code == 1
