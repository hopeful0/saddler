from .fetcher import (
    Fetcher,
    get_fetcher_cls,
    parse_source,
    register_fetcher,
)
from .model import (
    ResourceKind,
    ResourceSpec,
    SourceSpec,
)

__all__ = [
    "Fetcher",
    "ResourceKind",
    "ResourceSpec",
    "SourceSpec",
    "get_fetcher_cls",
    "parse_source",
    "register_fetcher",
]
