from __future__ import annotations

from dataclasses import dataclass

from ....agent.harness import get_harness_cls
from ....app.errors import NotFoundError
from ....runtime.backend import get_runtime_backend_cls
from ....shared.utils import generate_id
from ..api.service import GatewayApiService
from ..bridge.stdio import ACPStdioBridge


@dataclass
class AgentSession:
    session_id: str
    agent_id: str
    bridge: ACPStdioBridge

    @classmethod
    def create(cls, gateway_api: GatewayApiService, agent_ref: str) -> AgentSession:
        agent, runtime = gateway_api.get_agent_runtime(agent_ref)
        backend = get_runtime_backend_cls(runtime.spec.backend_type).load_state(
            runtime.spec, runtime.backend_state
        )
        if not backend.is_running():
            raise NotFoundError(f"Runtime is not running for agent {agent_ref!r}")
        harness = get_harness_cls(agent.spec.harness).from_spec(agent.spec)
        handle = harness.acp(backend, tty=False)
        return cls(
            session_id=generate_id(),
            agent_id=agent.id,
            bridge=ACPStdioBridge(handle),
        )

    async def close(self) -> None:
        await self.bridge.close()
