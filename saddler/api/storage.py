from __future__ import annotations

from dataclasses import dataclass

from ..app.storage import StorageUseCase
from ..storage.model import LocalStorageSpec, NFSStorageSpec, Storage, StorageSpec


@dataclass(frozen=True)
class StorageCreateRequest:
    type: str
    path: str | None = None
    server: str | None = None
    name: str | None = None
    metadata: dict[str, str] | None = None


class StorageApiService:
    def __init__(self, use_case: StorageUseCase) -> None:
        self._uc = use_case

    def create(self, req: StorageCreateRequest) -> Storage:
        spec = _build_spec(req)
        return self._uc.create_storage(spec, name=req.name, metadata=req.metadata)

    def remove(self, ref: str, *, force: bool = False) -> None:
        self._uc.remove_storage(ref)

    def list(self) -> list[Storage]:
        return self._uc.list_storages()

    def inspect(self, ref: str) -> Storage:
        return self._uc.get_storage(ref)


def _build_spec(req: StorageCreateRequest) -> StorageSpec:
    t = req.type.strip().lower()
    if t == "local":
        if not req.path:
            raise ValueError("--path is required for local storage")
        return LocalStorageSpec(path=req.path)
    if t == "nfs":
        if not req.server:
            raise ValueError("--server is required for nfs storage")
        if not req.path:
            raise ValueError("--path is required for nfs storage")
        return NFSStorageSpec(server=req.server, path=req.path)
    raise ValueError(f"Unknown storage type: {req.type!r} (expected: local, nfs)")
