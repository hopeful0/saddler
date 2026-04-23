from ..shared.repository import Repository
from ..storage.model import Storage, StorageSpec
from ..storage.service import StorageService
from .errors import ConflictError
from .resolver import NameResolver

_resolver = NameResolver[Storage]("Storage")


class StorageUseCase:
    def __init__(
        self, service: StorageService, repository: Repository[Storage]
    ) -> None:
        self._service = service
        self._repo = repository

    def create_storage(
        self,
        spec: StorageSpec,
        *,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Storage:
        return self._service.create_storage(spec, name=name, metadata=metadata)

    def remove_storage(self, ref: str) -> None:
        storage = _resolver.resolve(self._repo.list(), ref)
        if storage.mounted_by:
            raise ConflictError(
                f"Storage {storage.id!r} is mounted by runtimes: {storage.mounted_by}"
            )
        self._service.remove_storage(storage.id)

    def list_storages(self) -> list[Storage]:
        return self._service.list_storages()

    def get_storage(self, ref: str) -> Storage:
        return _resolver.resolve(self._repo.list(), ref)
