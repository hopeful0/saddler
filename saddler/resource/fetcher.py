from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Generator, Protocol

from .model import ResourceSpec, SourceSpec
from ..shared.registry import Registry


class Fetcher(Protocol):
    priority: int

    @classmethod
    def parse_source(cls, source: str) -> SourceSpec | None:
        """Try to parse the source string into a SourceSpec. Return None if the fetcher does not support this source."""

    @contextmanager
    def fetch_source(self, spec: SourceSpec) -> Generator[Path, None, None]:
        """Fetch the source and yield the path to the source root. The fetcher can clean up the source when the context is exited."""

    @contextmanager
    def fetch_resource(
        self, spec: ResourceSpec, source_root: Path | None
    ) -> Generator[Path, None, None]:
        """
        Fetch the resource and yield the path to the resource.
        If source_root is provided, the resource is fetched from the source root.
        The fetcher can clean up the resource when the context is exited.
        """


FETCHER_REGISTRY = Registry[type[Fetcher]](group="saddler.resource.fetcher")


def register_fetcher(name: str) -> Callable[[type[Fetcher]], type[Fetcher]]:
    def wrapper(cls: type[Fetcher]) -> type[Fetcher]:
        FETCHER_REGISTRY.register(name, cls)
        return cls

    return wrapper


def get_fetcher_cls(name: str) -> type[Fetcher]:
    return FETCHER_REGISTRY.get(name)


def parse_source(source: str) -> tuple[type[Fetcher], SourceSpec]:
    fetchers = list[type[Fetcher]](FETCHER_REGISTRY.items().values())
    fetchers.sort(key=lambda cls: getattr(cls, "priority", 10))
    for fetcher_cls in fetchers:
        spec = fetcher_cls.parse_source(source)
        if spec is not None:
            return fetcher_cls, spec
    raise ValueError(f"no fetcher for: {source}")
