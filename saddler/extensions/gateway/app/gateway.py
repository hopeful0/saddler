from __future__ import annotations

from ..api.service import GatewayApiService
from .session import AgentSession


class GatewayUseCase:
    def __init__(self, gateway_api: GatewayApiService) -> None:
        self._gateway_api = gateway_api
        self._sessions: dict[str, AgentSession] = {}

    async def create_session(self, agent_ref: str) -> AgentSession:
        session = AgentSession.create(self._gateway_api, agent_ref)
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def active_session_count(self) -> int:
        return len(self._sessions)

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        await session.close()

    async def close_all(self) -> None:
        for session_id in list(self._sessions):
            await self.close_session(session_id)
