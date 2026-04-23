from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Generic, Type, TypeVar

from filelock import FileLock
from pydantic import BaseModel

Entity = TypeVar("Entity", bound=BaseModel)

_DEFAULT_ROOT = Path.home() / ".saddler"
log = logging.getLogger(__name__)


class JsonFileRepository(Generic[Entity]):
    """
    File-backed Repository that stores each entity as an individual JSON file.

    Layout:
        <root>/<collection>/<id>.json

    Writes are protected by per-file locks (filelock) so concurrent saddler
    invocations do not corrupt state.
    """

    def __init__(
        self,
        model_cls: Type[Entity],
        collection: str,
        root: Path = _DEFAULT_ROOT,
    ) -> None:
        self._model_cls = model_cls
        self._dir = root / collection
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Repository protocol
    # ------------------------------------------------------------------

    def insert(self, entity: Entity) -> None:
        path = self._path(entity.id)  # type: ignore[attr-defined]
        lock = FileLock(str(path) + ".lock")
        with lock:
            if path.exists():
                raise FileExistsError(f"Entity already exists: {entity.id}")  # type: ignore[attr-defined]
            self._write(path, entity)

    def delete(self, id: str) -> None:
        path = self._path(id)
        lock = FileLock(str(path) + ".lock")
        with lock:
            path.unlink(missing_ok=True)

    def update(self, entity: Entity) -> None:
        path = self._path(entity.id)  # type: ignore[attr-defined]
        lock = FileLock(str(path) + ".lock")
        with lock:
            self._write(path, entity)

    def get(self, id: str) -> Entity | None:
        path = self._path(id)
        if not path.exists():
            return None
        return self._read(path)

    def list(self) -> list[Entity]:
        entities = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                entities.append(self._read(path))
            except Exception as exc:
                log.warning("Skipping unreadable record file: %s (%s)", path, exc)
        return entities

    def mutate(self, id: str, fn: Callable[[Entity], Entity]) -> None:
        path = self._path(id)
        lock = FileLock(str(path) + ".lock")
        with lock:
            if not path.exists():
                return
            entity = self._read(path)
            updated = fn(entity)
            self._write(path, updated)

    # ------------------------------------------------------------------

    def _path(self, id: str) -> Path:
        return self._dir / f"{id}.json"

    def _write(self, path: Path, entity: Entity) -> None:
        path.write_text(
            json.dumps(entity.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

    def _read(self, path: Path) -> Entity:
        return self._model_cls.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
