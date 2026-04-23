from .model import Storage, StorageSpec
from ..shared.repository import Repository
from ..shared.utils import generate_id


class StorageService:
    def __init__(self, repository: Repository[Storage]):
        self.repository = repository

    def create_storage(
        self,
        spec: StorageSpec,
        name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Storage:
        id = generate_id()
        storage = Storage(
            id=id,
            name=name,
            spec=spec,
            metadata=metadata,
        )
        self.repository.insert(storage)
        return storage

    def remove_storage(self, id: str) -> None:
        self.repository.delete(id)

    def list_storages(self) -> list[Storage]:
        return self.repository.list()

    def get_storage(self, id: str) -> Storage | None:
        return self.repository.get(id)
