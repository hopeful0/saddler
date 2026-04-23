from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

import saddler.cli as cli
from saddler.cli import app


class _DummyRuntimeApi:
    def __init__(self) -> None:
        self.last_request = None

    def create(self, request):  # noqa: ANN001
        self.last_request = request
        return SimpleNamespace(id="rt-123", name=request.name)


def _install_dummy_runtime_api(monkeypatch) -> _DummyRuntimeApi:  # noqa: ANN001
    api = _DummyRuntimeApi()
    monkeypatch.setattr(cli, "_runtime_api", lambda: api)
    return api


def test_runtime_create_local_subcommand(monkeypatch) -> None:
    api = _install_dummy_runtime_api(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "create",
            "local",
            "--name",
            "local-rt",
            "--opt",
            "foo=bar",
        ],
    )

    assert result.exit_code == 0
    assert api.last_request.backend_type == "local"
    assert api.last_request.backend_spec == {"foo": "bar"}
    assert "Created runtime local-rt (rt-123)" in result.stdout
    assert "Next: saddler runtime start local-rt" in result.stdout


def test_runtime_create_docker_subcommand_named_flag_overrides_opt(
    monkeypatch, tmp_path
) -> None:
    api = _install_dummy_runtime_api(monkeypatch)
    host_dir = tmp_path / "host-data"
    host_dir.mkdir()

    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "create",
            "docker",
            "--opt",
            "image=from-opt",
            "--image",
            "from-flag",
            "--user",
            "root",
            "--mount",
            f"{host_dir}:/workspace",
        ],
    )

    assert result.exit_code == 0
    assert api.last_request.backend_type == "docker"
    assert api.last_request.backend_spec == {"image": "from-flag", "user": "root"}
    assert len(api.last_request.mounts) == 1
    assert api.last_request.mounts[0].type == "bind"


def test_runtime_create_generic_custom_type_with_opt(monkeypatch) -> None:
    api = _install_dummy_runtime_api(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "create",
            "--type",
            "custom-plugin",
            "--opt",
            "endpoint=http://localhost",
            "--opt",
            "token=abc",
        ],
    )

    assert result.exit_code == 0
    assert api.last_request.backend_type == "custom-plugin"
    assert api.last_request.backend_spec == {
        "endpoint": "http://localhost",
        "token": "abc",
    }


def test_runtime_create_opt_repeated_key_uses_last_value(monkeypatch) -> None:
    api = _install_dummy_runtime_api(monkeypatch)

    result = CliRunner().invoke(
        app,
        [
            "runtime",
            "create",
            "--type",
            "custom-plugin",
            "--opt",
            "token=old",
            "--opt",
            "token=new",
        ],
    )

    assert result.exit_code == 0
    assert api.last_request.backend_spec == {"token": "new"}


def test_runtime_create_opt_invalid_format(monkeypatch) -> None:
    _install_dummy_runtime_api(monkeypatch)

    result = CliRunner().invoke(
        app,
        ["runtime", "create", "--type", "custom-plugin", "--opt", "invalid"],
    )

    assert result.exit_code != 0
    assert "expected KEY=VALUE" in result.stderr


def test_runtime_create_generic_help_shows_subcommands() -> None:
    result = CliRunner().invoke(app, ["runtime", "create", "--help"])

    assert result.exit_code == 0
    assert "local" in result.stdout
    assert "docker" in result.stdout
    assert "-opt" in result.stdout
    assert "--image" not in result.stdout
    assert "--user" not in result.stdout
    assert "--mount" not in result.stdout
