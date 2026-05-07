from __future__ import annotations

from ..api.service import GatewayApiService
from .session import AgentSession
from .tty_session import TtySession


class GatewayUseCase:
    def __init__(self, gateway_api: GatewayApiService) -> None:
        self._gateway_api = gateway_api
        self._sessions: dict[str, AgentSession] = {}
        self._tty_sessions: dict[str, TtySession] = {}

    async def create_session(self, agent_ref: str) -> AgentSession:
        session = AgentSession.create(self._gateway_api, agent_ref)
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def active_session_count(self) -> int:
        return len(self._sessions) + len(self._tty_sessions)

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        await session.close()

    async def create_tty_session(self, agent_ref: str) -> TtySession:
        session = TtySession.create(self._gateway_api, agent_ref)
        self._tty_sessions[session.session_id] = session
        return session

    async def close_tty_session(self, session_id: str) -> None:
        session = self._tty_sessions.pop(session_id, None)
        if session is None:
            return
        await session.close()

    async def close_all(self) -> None:
        for session_id in list(self._sessions):
            await self.close_session(session_id)
        for session_id in list(self._tty_sessions):
            await self.close_tty_session(session_id)
