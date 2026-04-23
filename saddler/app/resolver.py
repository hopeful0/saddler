from __future__ import annotations

from typing import Generic, Protocol, TypeVar

from .errors import AmbiguousIdentifierError, NotFoundError, ValidationError


class _HasId(Protocol):
    id: str
    name: str | None


E = TypeVar("E", bound=_HasId)


class NameResolver(Generic[E]):
    """Resolve a name/id/prefix reference to a single entity."""

    def __init__(self, entity_label: str) -> None:
        self._label = entity_label

    def resolve(self, entities: list[E], ref: str) -> E:
        ref = ref.strip()
        if not ref:
            raise ValidationError(f"{self._label} reference must not be empty")

        # Exact ID match
        for e in entities:
            if e.id == ref:
                return e

        # Exact name match
        for e in entities:
            if e.name == ref:
                return e

        # ID prefix match
        prefix_matches = [e for e in entities if e.id.startswith(ref)]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(prefix_matches) > 1:
            preview = ", ".join(f"{e.id}({e.name or '-'})" for e in prefix_matches[:5])
            raise AmbiguousIdentifierError(
                f"Ambiguous {self._label} prefix {ref!r}. Matches: {preview}"
            )

        raise NotFoundError(f"{self._label} not found: {ref!r}")
