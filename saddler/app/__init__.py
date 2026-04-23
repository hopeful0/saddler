"""
Application (UseCase) layer.

Typical wiring for a single-process deployment:

    ucs = build_use_cases()           # persists to ~/.saddler/
    ucs = build_use_cases(persist=False)  # in-memory only (tests/dev)
"""

from dataclasses import dataclass
from pathlib import Path

from ..agent.model import Agent
from ..agent.service import AgentService
from ..runtime.model import Runtime
from ..runtime.service import RuntimeService
from ..shared.repository import Repository
from ..storage.model import Storage
from ..storage.service import StorageService
from .agent import AgentUseCase
from .errors import (
    AppError,
    AmbiguousIdentifierError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from .runtime import RuntimeUseCase
from .storage import StorageUseCase

_DEFAULT_ROOT = Path.home() / ".saddler"


@dataclass
class UseCases:
    storage: StorageUseCase
    runtime: RuntimeUseCase
    agent: AgentUseCase


def build_use_cases(
    *,
    persist: bool = True,
    root: Path = _DEFAULT_ROOT,
    storage_repo: Repository[Storage] | None = None,
    runtime_repo: Repository[Runtime] | None = None,
    agent_repo: Repository[Agent] | None = None,
) -> UseCases:
    """
    Wire domain services and repositories into use cases.

    Args:
        persist:      When True (default) use JsonFileRepository under ``root``.
                      When False use InMemoryRepository (tests / ephemeral use).
        root:         Base directory for JsonFileRepository. Defaults to ~/.saddler.
        *_repo:       Override individual repositories; takes precedence over persist/root.
    """
    s_repo: Repository[Storage] = storage_repo or _make_repo(
        Storage, "storages", persist, root
    )
    r_repo: Repository[Runtime] = runtime_repo or _make_repo(
        Runtime, "runtimes", persist, root
    )
    a_repo: Repository[Agent] = agent_repo or _make_repo(Agent, "agents", persist, root)

    storage_svc = StorageService(s_repo)
    runtime_svc = RuntimeService(r_repo)
    agent_svc = AgentService(a_repo)

    return UseCases(
        storage=StorageUseCase(storage_svc, s_repo),
        runtime=RuntimeUseCase(runtime_svc, r_repo, s_repo),
        agent=AgentUseCase(agent_svc, a_repo, r_repo),
    )


def _make_repo(model_cls, collection: str, persist: bool, root: Path):
    if persist:
        from ..infra.store.json_file import JsonFileRepository

        return JsonFileRepository(model_cls, collection, root)
    from ..infra.store.memory import InMemoryRepository

    return InMemoryRepository()


__all__ = [
    "UseCases",
    "build_use_cases",
    "StorageUseCase",
    "RuntimeUseCase",
    "AgentUseCase",
    "AppError",
    "NotFoundError",
    "AmbiguousIdentifierError",
    "ConflictError",
    "ValidationError",
]
