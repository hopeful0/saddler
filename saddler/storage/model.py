from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..shared.registry import Registry
from ..shared.types import PosixAbsolutePath
from ..shared.utils import resolve_model_str_field_value


class StorageType(StrEnum):
    LOCAL = "local"
    NFS = "nfs"


class BaseStorageSpec(BaseModel):
    type: StorageType


STORAGE_SPEC_REGISTRY = Registry[type[BaseStorageSpec]](group="saddler.storage_spec")

StorageSpec = BaseStorageSpec


def register_storage_spec(cls: type[BaseStorageSpec]) -> type[BaseStorageSpec]:
    spec_type = resolve_model_str_field_value(cls, "type")
    STORAGE_SPEC_REGISTRY.register(spec_type, cls)
    return cls


@register_storage_spec
class LocalStorageSpec(BaseStorageSpec):
    type: Literal[StorageType.LOCAL] = StorageType.LOCAL
    path: PosixAbsolutePath


@register_storage_spec
class NFSStorageSpec(BaseStorageSpec):
    type: Literal[StorageType.NFS] = StorageType.NFS
    server: str
    path: PosixAbsolutePath


class Storage(BaseModel):
    id: str
    name: str | None = None
    metadata: dict[str, str] | None = None
    spec: StorageSpec
    mounted_by: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Runtime IDs that are mounted this storage",
        ),
    ]
