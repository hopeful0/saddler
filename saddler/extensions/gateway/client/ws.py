from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse, urlunparse

import websockets


def gateway_agent_ws_uri(base_url: str, agent_ref: str) -> str:
    parsed = urlparse(base_url)
    scheme = parsed.scheme.lower()
    if scheme == "http":
        ws_scheme = "ws"
    elif scheme == "https":
        ws_scheme = "wss"
    elif scheme in ("ws", "wss"):
        ws_scheme = scheme
    else:
        msg = f"unsupported URL scheme for gateway WebSocket: {parsed.scheme!r}"
        raise ValueError(msg)
    path = parsed.path.rstrip("/") + f"/agents/{agent_ref}/ws"
    return urlunparse(
        (ws_scheme, parsed.netloc, path, "", parsed.query, parsed.fragment)
    )


class WsRemoteSession:
    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._closed = False

    async def send(self, msg: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(msg, ensure_ascii=False))

    async def recv(self) -> dict[str, Any]:
        raw = await self._ws.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._ws.close()

    @staticmethod
    async def connect(url: str, agent_ref: str, token: str) -> WsRemoteSession:
        uri = gateway_agent_ws_uri(url, agent_ref)
        headers = [("Authorization", f"Bearer {token}")]
        ws = await websockets.connect(uri, additional_headers=headers)
        return WsRemoteSession(ws)
