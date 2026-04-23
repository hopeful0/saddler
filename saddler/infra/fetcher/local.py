from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from ...resource import ResourceSpec, SourceSpec, register_fetcher
from .utils import find_resource


@register_fetcher("local")
class LocalFetcher:
    priority = 0

    @classmethod
    def parse_source(cls, source: str) -> SourceSpec | None:
        path = Path(source.strip()).expanduser()
        if not path.exists() or not path.is_dir():
            return None
        return SourceSpec(kind="local", uri=str(path))

    @contextmanager
    def fetch_source(self, spec: SourceSpec) -> Generator[Path, None, None]:
        if spec.kind != "local":
            raise ValueError("LocalFetcher requires SourceSpec.kind=local")
        source_path = Path(spec.uri).expanduser().resolve()
        if not source_path.is_dir():
            raise ValueError(f"local source not found: {spec.uri}")
        yield source_path

    @contextmanager
    def fetch_resource(
        self, spec: ResourceSpec, source_root: Path | None
    ) -> Generator[Path, None, None]:
        if source_root is None:
            if isinstance(spec.source, SourceSpec):
                source_spec = spec.source
            else:
                source_spec = type(self).parse_source(spec.source)
                if source_spec is None:
                    raise ValueError(f"LocalFetcher cannot parse source: {spec.source}")
            with self.fetch_source(source_spec) as resolved_root:
                yield find_resource(spec, resolved_root)
                return
        yield find_resource(spec, source_root)
