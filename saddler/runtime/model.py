from enum import StrEnum
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, Field, JsonValue

from ..shared.types import PosixAbsolutePath


RuntimeBackendStateT = TypeVar("RuntimeBackendStateT")


class RuntimeStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    REMOVED = "removed"


class RuntimeMountType(StrEnum):
    BIND = "bind"
    STORAGE = "storage"


class RuntimeMountMode(StrEnum):
    RO = "ro"
    RW = "rw"


class BaseRuntimeMount(BaseModel):
    type: RuntimeMountType
    destination: PosixAbsolutePath
    mode: RuntimeMountMode = RuntimeMountMode.RW


class RuntimeStorageMount(BaseRuntimeMount):
    type: Literal[RuntimeMountType.STORAGE] = RuntimeMountType.STORAGE
    storage_id: str = Field(min_length=1)


class RuntimeHostBindMount(BaseRuntimeMount):
    type: Literal[RuntimeMountType.BIND] = RuntimeMountType.BIND
    source: PosixAbsolutePath


RuntimeMount = Annotated[
    RuntimeStorageMount | RuntimeHostBindMount, Field(discriminator="type")
]


class RuntimeSpec(BaseModel):
    backend_type: str
    env: Annotated[dict[str, str], Field(default_factory=dict)]
    mounts: Annotated[list[RuntimeMount], Field(default_factory=list)]
    backend_spec: JsonValue | None = None


class Runtime(BaseModel):
    id: str
    name: str | None = None
    metadata: dict[str, str] | None = None
    spec: RuntimeSpec
    backend_state: JsonValue | None
    used_by: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Agent IDs referencing this runtime.",
        ),
    ]
