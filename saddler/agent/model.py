from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from ..resource.model import ResourceSpec
from ..shared.types import PosixAbsolutePath


class SkillSpec(ResourceSpec):
    kind: Literal["skill"] = "skill"
    source: str


class RuleSpec(ResourceSpec):
    kind: Literal["rule"] = "rule"
    source: str


class RoleSpec(RuleSpec):
    name: Literal["role"] = "role"


class AgentSpec(BaseModel):
    """Immutable agent definition (harness + workspace inputs)."""

    harness: str
    workdir: Annotated[
        PosixAbsolutePath,
        Field(
            description="Working directory inside the runtime (posix absolute path).",
        ),
    ]
    role: RoleSpec | None
    skills: Annotated[list[SkillSpec], Field(default_factory=list)]
    rules: Annotated[list[RuleSpec], Field(default_factory=list)]

    harness_spec: JsonValue | None


class Agent(BaseModel):
    id: str
    name: str | None = None
    metadata: dict[str, str] | None = None
    runtime: Annotated[str, Field(min_length=1, description="ID of runtime to use")]
    spec: AgentSpec
