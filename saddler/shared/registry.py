from __future__ import annotations

from collections.abc import Callable, Iterable
from importlib.metadata import EntryPoint, entry_points
from typing import Generic, TypeVar

ItemT = TypeVar("ItemT")


class Registry(Generic[ItemT]):
    """Generic key-value registry with optional entry point discovery."""

    def __init__(
        self,
        *,
        group: str | None = None,
        loader: Callable[[], Iterable[EntryPoint]] | None = None,
    ) -> None:
        self._group = group
        self._loader = loader
        self._items: dict[str, ItemT] = {}
        self._loaded = False

    def register(self, name: str, item: ItemT, *, overwrite: bool = False) -> None:
        if not name:
            raise ValueError("Registry key cannot be empty")
        if not overwrite and name in self._items:
            raise ValueError(f"Registry key already exists: {name}")
        self._items[name] = item

    def get(self, name: str) -> ItemT:
        self.load_entry_points()
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"Unknown registry key: {name}") from exc

    def get_or_none(self, name: str) -> ItemT | None:
        self.load_entry_points()
        return self._items.get(name)

    def list(self) -> list[str]:
        self.load_entry_points()
        return sorted(self._items)

    def items(self) -> dict[str, ItemT]:
        self.load_entry_points()
        return dict(self._items)

    def load_entry_points(self, *, force: bool = False) -> None:
        if self._loaded and not force:
            return

        for ep in self._iter_entry_points():
            loaded = ep.load()
            self.register(ep.name, loaded, overwrite=True)
        self._loaded = True

    def _iter_entry_points(self) -> Iterable[EntryPoint]:
        if self._loader is not None:
            return self._loader()

        if self._group is None:
            return ()

        return entry_points(group=self._group)
