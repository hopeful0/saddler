from __future__ import annotations

import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from pydantic import BaseModel

from ...resource import ResourceSpec, SourceSpec, register_fetcher
from .utils import find_resource

# Matches remote git URLs or local paths ending with .git
_GIT_URI_RE = re.compile(r"^(?:https?://|git://|ssh://|git@)|\.git$")


class GitFetcherSpec(BaseModel):
    ref: str | None = None


@register_fetcher("git")
class GitFetcher:
    priority = 1

    @classmethod
    def parse_source(cls, source: str) -> SourceSpec[GitFetcherSpec] | None:
        s = source.strip()
        ref: str | None = None
        if "#" in s:
            s, fragment = s.rsplit("#", 1)
            ref = fragment or None
        if not _GIT_URI_RE.search(s):
            return None
        return SourceSpec(kind="git", uri=s, fetcher_spec=GitFetcherSpec(ref=ref))

    @contextmanager
    def fetch_source(
        self, spec: SourceSpec[GitFetcherSpec]
    ) -> Generator[Path, None, None]:
        if spec.kind != "git":
            raise ValueError("GitFetcher requires SourceSpec.kind=git")
        ref = spec.fetcher_spec.ref if spec.fetcher_spec else None

        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = ["git", "clone"]
            if ref:
                cmd += ["--branch", ref, "--single-branch"]
            cmd += [spec.uri, tmpdir]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            yield Path(tmpdir)

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
                    raise ValueError(f"GitFetcher cannot parse source: {spec.source}")
            with self.fetch_source(source_spec) as resolved_root:
                yield find_resource(spec, resolved_root)
                return
        yield find_resource(spec, source_root)
