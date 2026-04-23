from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

import saddler.cli as cli
from saddler.cli import app


class _DummyRuntimeApi:
    def __init__(self, runtime) -> None:  # noqa: ANN001
        self._runtime = runtime

    def inspect(self, ref: str):  # noqa: ANN001
        assert ref == "rt-demo"
        return self._runtime


class _DummyBackendLoader:
    @classmethod
    def load_state(cls, _spec, _state):  # noqa: ANN001
        return object()


def test_agent_create_rejects_unknown_harness(monkeypatch) -> None:
    monkeypatch.setattr(cli.AGENT_HARNESS_REGISTRY, "list", lambda: ["cursor", "codex"])

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "create",
            "--runtime",
            "rt-1",
            "--harness",
            "unknown-h",
            "--workdir",
            "/workspace",
        ],
    )

    assert result.exit_code != 0
    assert "Unknown harness" in result.stderr
    assert "cursor, codex" in result.stderr


def test_runtime_harnesses_shows_installed_status(monkeypatch) -> None:
    runtime = SimpleNamespace(
        id="rt-demo",
        name="demo",
        spec=SimpleNamespace(backend_type="docker"),
        backend_state={"container_id": "abc"},
    )
    monkeypatch.setattr(cli, "_runtime_api", lambda: _DummyRuntimeApi(runtime))
    monkeypatch.setattr(cli, "get_runtime_backend_cls", lambda _t: _DummyBackendLoader)
    monkeypatch.setattr(cli.AGENT_HARNESS_REGISTRY, "list", lambda: ["cursor", "codex"])

    class _InstalledHarness:
        @classmethod
        def from_spec(cls, _spec):  # noqa: ANN001
            return cls()

        def is_installed(self, _backend):  # noqa: ANN001
            return True

    class _MissingHarness:
        @classmethod
        def from_spec(cls, _spec):  # noqa: ANN001
            return cls()

        def is_installed(self, _backend):  # noqa: ANN001
            return False

    def _get_harness_cls(name: str):
        return _InstalledHarness if name == "cursor" else _MissingHarness

    monkeypatch.setattr(cli, "get_harness_cls", _get_harness_cls)

    result = CliRunner().invoke(app, ["runtime", "harnesses", "rt-demo"])

    assert result.exit_code == 0
    assert "Runtime demo (rt-demo)" in result.stdout
    assert "cursor" in result.stdout and "installed" in result.stdout
    assert "codex" in result.stdout and "not installed" in result.stdout


def test_runtime_help_includes_harnesses_command() -> None:
    result = CliRunner().invoke(app, ["runtime", "--help"])

    assert result.exit_code == 0
    assert "harnesses" in result.stdout
