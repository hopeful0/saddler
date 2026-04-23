from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

import saddler.cli as cli
from saddler.cli import app


class _DummyRuntimeApi:
    def __init__(self, entries) -> None:  # noqa: ANN001
        self._entries = entries

    def list_with_status(self):  # noqa: ANN001
        return self._entries


def _runtime_record(
    *,
    runtime_id: str,
    name: str,
    backend_type: str,
    used_by: list[str],
):
    return SimpleNamespace(
        id=runtime_id,
        name=name,
        spec=SimpleNamespace(backend_type=backend_type),
        used_by=used_by,
    )


def test_runtime_ls_shows_status_column(monkeypatch) -> None:
    entries = [
        (
            _runtime_record(
                runtime_id="rt-1", name="alpha", backend_type="local", used_by=["a1"]
            ),
            "running",
        ),
        (
            _runtime_record(
                runtime_id="rt-2", name="beta", backend_type="docker", used_by=[]
            ),
            "not running",
        ),
    ]
    monkeypatch.setattr(cli, "_runtime_api", lambda: _DummyRuntimeApi(entries))

    result = CliRunner().invoke(app, ["runtime", "ls"])

    assert result.exit_code == 0
    assert "STATUS" in result.stdout
    assert "running" in result.stdout
    assert "not running" in result.stdout


def test_runtime_ls_handles_empty_entries(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_runtime_api", lambda: _DummyRuntimeApi([]))

    result = CliRunner().invoke(app, ["runtime", "ls"])

    assert result.exit_code == 0
    assert "No runtimes found." in result.stdout
