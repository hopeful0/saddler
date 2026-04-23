from __future__ import annotations

from types import SimpleNamespace

import saddler.cli as cli


class _DummyListApi:
    def __init__(self, items) -> None:  # noqa: ANN001
        self._items = items

    def list(self):  # noqa: ANN001
        return self._items


def test_complete_runtime_ref_matches_name_and_id(monkeypatch) -> None:
    api = _DummyListApi(
        [
            SimpleNamespace(id="rt-001", name="alpha"),
            SimpleNamespace(id="rt-002", name=None),
        ]
    )
    monkeypatch.setattr(cli, "_runtime_api", lambda: api)

    assert cli._complete_runtime_ref("rt-") == ["rt-001", "rt-002"]
    assert cli._complete_runtime_ref("al") == ["alpha"]


def test_complete_agent_ref_matches_name_and_id(monkeypatch) -> None:
    api = _DummyListApi(
        [
            SimpleNamespace(id="ag-001", name="builder"),
            SimpleNamespace(id="ag-002", name="tester"),
        ]
    )
    monkeypatch.setattr(cli, "_agent_api", lambda: api)

    assert cli._complete_agent_ref("ag-") == ["ag-001", "ag-002"]
    assert cli._complete_agent_ref("te") == ["tester"]


def test_complete_storage_ref_matches_name_and_id(monkeypatch) -> None:
    api = _DummyListApi(
        [
            SimpleNamespace(id="st-001", name="cache"),
            SimpleNamespace(id="st-002", name=None),
        ]
    )
    monkeypatch.setattr(cli, "_storage_api", lambda: api)

    assert cli._complete_storage_ref("st-") == ["st-001", "st-002"]
    assert cli._complete_storage_ref("ca") == ["cache"]


def test_dynamic_completion_fails_open(monkeypatch) -> None:
    monkeypatch.setattr(
        cli, "_runtime_api", lambda: (_ for _ in ()).throw(RuntimeError)
    )
    monkeypatch.setattr(cli, "_agent_api", lambda: (_ for _ in ()).throw(RuntimeError))
    monkeypatch.setattr(
        cli, "_storage_api", lambda: (_ for _ in ()).throw(RuntimeError)
    )
    monkeypatch.setattr(
        cli.RUNTIME_BACKEND_REGISTRY,
        "list",
        lambda: (_ for _ in ()).throw(RuntimeError),
    )

    assert cli._complete_runtime_ref("x") == []
    assert cli._complete_agent_ref("x") == []
    assert cli._complete_storage_ref("x") == []
    assert cli._complete_backend_type("x") == []


def test_complete_storage_type_filters_prefix() -> None:
    assert cli._complete_storage_type("l") == ["local"]
    assert cli._complete_storage_type("n") == ["nfs"]
