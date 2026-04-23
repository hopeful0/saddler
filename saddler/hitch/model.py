from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class HitchStorageDef(BaseModel):
    """Mirrors StorageCreateRequest: type + path/server."""

    type: str  # "local" | "nfs"
    path: str | None = None  # host path; relative → resolved vs compose dir
    server: str | None = None  # NFS server (nfs only)
    name: str | None = None
    depends_on: Annotated[list[str], Field(default_factory=list)]


class HitchBindMount(BaseModel):
    type: Literal["bind"]
    source: str  # host path; relative → resolved vs compose dir
    destination: str  # absolute posix path inside runtime
    mode: str = "rw"


class HitchStorageMount(BaseModel):
    type: Literal["storage"]
    storage: str  # logical storage service_id in this config
    destination: str  # absolute posix path inside runtime
    mode: str = "rw"


HitchMountSpec = HitchBindMount | HitchStorageMount


class HitchRuntimeDef(BaseModel):
    """Mirrors RuntimeCreateRequest: backend + env + mounts + backend_spec."""

    backend: str  # "docker" | "local" | any registered backend_type
    name: str | None = None
    env: Annotated[dict[str, str], Field(default_factory=dict)]
    mounts: Annotated[list[HitchMountSpec], Field(default_factory=list)]
    backend_spec: dict | None = (
        None  # passed through to RuntimeCreateRequest.backend_spec
    )
    depends_on: Annotated[list[str], Field(default_factory=list)]


class HitchResourceSpec(BaseModel):
    """Mirrors ResourceCreateSpec: name + source + optional path."""

    name: str
    source: str  # path or URL; relative paths resolved vs compose dir
    path: str | None = None  # sub-path within source (e.g. in a git repo)


class HitchRoleSpec(BaseModel):
    """Role has no user-facing name (always 'role' internally)."""

    source: str
    path: str | None = None


class HitchAgentDef(BaseModel):
    """Mirrors AgentCreateRequest: harness + workdir + runtime ref + resources."""

    harness: str
    workdir: str  # absolute posix path inside runtime
    runtime: str  # logical runtime service_id, or external runtime name/id
    name: str | None = None
    role: HitchRoleSpec | None = None
    skills: Annotated[list[HitchResourceSpec], Field(default_factory=list)]
    rules: Annotated[list[HitchResourceSpec], Field(default_factory=list)]
    depends_on: Annotated[list[str], Field(default_factory=list)]


class HitchConfig(BaseModel):
    name: str | None = None
    version: int = 1
    storages: Annotated[dict[str, HitchStorageDef], Field(default_factory=dict)]
    runtimes: Annotated[dict[str, HitchRuntimeDef], Field(default_factory=dict)]
    agents: Annotated[dict[str, HitchAgentDef], Field(default_factory=dict)]

    @model_validator(mode="before")
    @classmethod
    def _normalize_string_mounts(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        storages = data.get("storages")
        storage_ids = set(storages.keys()) if isinstance(storages, dict) else set()

        runtimes = data.get("runtimes")
        if not isinstance(runtimes, dict):
            return data

        for runtime_def in runtimes.values():
            if not isinstance(runtime_def, dict):
                continue
            mounts = runtime_def.get("mounts")
            if not isinstance(mounts, list):
                continue
            runtime_def["mounts"] = [
                _parse_mount_string(m, storage_ids) if isinstance(m, str) else m
                for m in mounts
            ]

        return data


def _parse_mount_string(value: str, storage_ids: set[str]) -> dict:
    parts = value.split(":")
    if len(parts) < 2:
        raise ValueError(
            f"Invalid mount string {value!r}. Expected 'src:dst' or 'src:dst:ro|rw'"
        )

    mode = "rw"
    if parts[-1] in {"ro", "rw"}:
        mode = parts.pop()

    if len(parts) < 2:
        raise ValueError(
            f"Invalid mount string {value!r}. Expected 'src:dst' or 'src:dst:ro|rw'"
        )

    destination = parts[-1]
    source_or_storage = ":".join(parts[:-1])

    if source_or_storage in storage_ids:
        return {
            "type": "storage",
            "storage": source_or_storage,
            "destination": destination,
            "mode": mode,
        }

    return {
        "type": "bind",
        "source": source_or_storage,
        "destination": destination,
        "mode": mode,
    }
