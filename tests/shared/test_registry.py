from pathlib import Path
import sys
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from saddler.shared.registry import Registry


class _FakeEntryPoint:
    def __init__(self, name: str, value: Any) -> None:
        self.name = name
        self._value = value

    def load(self) -> Any:
        return self._value


def test_registry_register_and_get() -> None:
    registry = Registry[str]()
    registry.register("local", "adapter")

    assert registry.get("local") == "adapter"
    assert registry.list() == ["local"]


def test_registry_register_duplicate_fails() -> None:
    registry = Registry[str]()
    registry.register("local", "v1")

    with pytest.raises(ValueError, match="already exists"):
        registry.register("local", "v2")


def test_registry_discovers_from_entry_points() -> None:
    def loader() -> list[_FakeEntryPoint]:
        return [
            _FakeEntryPoint("bind", "bind-handler"),
            _FakeEntryPoint("storage", "storage-handler"),
        ]

    registry = Registry[str](loader=loader)
    assert registry.get("bind") == "bind-handler"
    assert registry.list() == ["bind", "storage"]


def test_registry_entry_points_loaded_once_unless_forced() -> None:
    calls = {"count": 0}

    def loader() -> list[_FakeEntryPoint]:
        calls["count"] += 1
        return [_FakeEntryPoint("bind", "bind-handler")]

    registry = Registry[str](loader=loader)
    registry.load_entry_points()
    registry.load_entry_points()
    registry.load_entry_points(force=True)

    assert calls["count"] == 2


def test_registry_get_unknown_raises_helpful_error() -> None:
    registry = Registry[str]()

    with pytest.raises(KeyError, match="Unknown registry key"):
        registry.get("missing")
