from __future__ import annotations
from typing import Generic, Literal, TypeVar


from pydantic import BaseModel, Field, JsonValue, field_validator

ResourceKind = Literal["skill", "rule"]
SpecT = TypeVar("SpecT", bound=JsonValue | BaseModel)


class SourceSpec(BaseModel, Generic[SpecT]):
    kind: str
    uri: str = Field(min_length=1)
    fetcher_spec: SpecT | None = None
    hash: str | None = None


class ResourceSpec(BaseModel):
    kind: ResourceKind
    name: str = Field(min_length=1)
    source: str | SourceSpec
    path: str | None = None
    hash: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must be non-empty")
        return stripped

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str | SourceSpec) -> str | SourceSpec:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("source must be non-empty")
            return stripped
        return value
