from typing import Callable, Generic, TypeVar

Entity = TypeVar("Entity")


class InMemoryRepository(Generic[Entity]):
    """In-memory Repository — suitable for tests and ephemeral runs."""

    def __init__(self) -> None:
        self._store: dict[str, Entity] = {}

    def insert(self, entity: Entity) -> None:
        self._store[entity.id] = entity  # type: ignore[attr-defined]

    def delete(self, id: str) -> None:
        self._store.pop(id, None)

    def update(self, entity: Entity) -> None:
        self._store[entity.id] = entity  # type: ignore[attr-defined]

    def get(self, id: str) -> Entity | None:
        return self._store.get(id)

    def list(self) -> list[Entity]:
        return list(self._store.values())

    def mutate(self, id: str, fn: Callable[[Entity], Entity]) -> None:
        entity = self._store.get(id)
        if entity is not None:
            self._store[id] = fn(entity)
