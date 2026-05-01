from __future__ import annotations

import json
from typing import Any

import httpx


class HttpRemoteSession:
    def __init__(self, client: httpx.AsyncClient, session_id: str) -> None:
        self._client = client
        self._session_id = session_id
        self._stream_cm: Any = None
        self._stream_response: httpx.Response | None = None
        self._line_iter: Any = None
        self._closed = False

    @classmethod
    async def connect(cls, url: str, agent_ref: str, token: str) -> HttpRemoteSession:
        base = url.rstrip("/")
        client = httpx.AsyncClient(
            base_url=base,
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            r = await client.post(f"/agents/{agent_ref}/sessions")
            r.raise_for_status()
            session_id = r.json()["session_id"]
        except BaseException:
            await client.aclose()
            raise
        return cls(client, session_id)

    async def send(self, msg: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("session is closed")
        r = await self._client.post(
            f"/sessions/{self._session_id}/input",
            json=msg,
        )
        r.raise_for_status()

    async def _ensure_stream(self) -> None:
        if self._line_iter is not None:
            return
        self._stream_cm = self._client.stream(
            "GET",
            f"/sessions/{self._session_id}/stream",
            headers={"Accept": "text/event-stream"},
        )
        self._stream_response = await self._stream_cm.__aenter__()
        self._stream_response.raise_for_status()
        self._line_iter = self._stream_response.aiter_lines()

    async def recv(self) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("session is closed")
        await self._ensure_stream()
        assert self._line_iter is not None
        while True:
            try:
                line = await self._line_iter.__anext__()
            except StopAsyncIteration as exc:
                raise EOFError("SSE stream ended") from exc
            line = line.strip("\r")
            if line == "" or line.startswith(":"):
                continue
            prefix = "data: "
            if line.startswith(prefix):
                return json.loads(line[len(prefix) :])

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stream_cm is not None:
            await self._stream_cm.__aexit__(None, None, None)
            self._stream_cm = None
            self._stream_response = None
            self._line_iter = None
        try:
            r = await self._client.delete(f"/sessions/{self._session_id}")
            if r.status_code not in (204, 404):
                r.raise_for_status()
        finally:
            await self._client.aclose()
