from __future__ import annotations

from typing import Any, Literal

from .http import HttpRemoteSession
from .ws import WsRemoteSession

TransportName = Literal["ws", "http"]


class GatewayClient:
    def __init__(self, url: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._token = token
        self._sessions: list[Any] = []

    async def connect(
        self,
        agent_ref: str,
        transport: TransportName = "ws",
    ) -> WsRemoteSession | HttpRemoteSession:
        if transport == "ws":
            session = await WsRemoteSession.connect(self._url, agent_ref, self._token)
        elif transport == "http":
            session = await HttpRemoteSession.connect(self._url, agent_ref, self._token)
        else:
            msg = f"unknown transport: {transport!r}"
            raise ValueError(msg)
        self._sessions.append(session)
        return session

    async def __aenter__(self) -> GatewayClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        for session in list(self._sessions):
            await session.close()
        self._sessions.clear()


__all__ = ["GatewayClient", "HttpRemoteSession", "WsRemoteSession"]
