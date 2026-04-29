from __future__ import annotations

from ....api.agent import AgentApiService
from ....api.runtime import RuntimeApiService
from ....agent.model import Agent
from ....runtime.model import Runtime


class GatewayApiService:
    def __init__(
        self, agent_api: AgentApiService, runtime_api: RuntimeApiService
    ) -> None:
        self.agent_api = agent_api
        self.runtime_api = runtime_api

    def get_agent_runtime(self, agent_ref: str) -> tuple[Agent, Runtime]:
        agent = self.agent_api.inspect(agent_ref)
        runtime = self.runtime_api.inspect(agent.runtime)
        return agent, runtime
