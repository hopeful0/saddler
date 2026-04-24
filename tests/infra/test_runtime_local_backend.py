from saddler.infra.runtime.local import LocalRuntimeBackend
from saddler.runtime.model import RuntimeSpec


def _make_backend() -> LocalRuntimeBackend:
    return LocalRuntimeBackend(spec=RuntimeSpec(backend_type="local"))


def test_exec_supports_shell_features_for_string_command() -> None:
    backend = _make_backend()

    result = backend.exec("printf 'alpha\\nbeta\\n' | wc -l", cwd=".")

    assert result.exit_code == 0
    assert result.stdout.strip() == "2"


def test_exec_supports_shell_semantics_for_list_command() -> None:
    backend = _make_backend()

    result = backend.exec(["echo", "foo | tr o a"], cwd=".")

    assert result.exit_code == 0
    assert result.stdout.strip() == "foo | tr o a"


def test_exec_fg_raises_runtime_error_on_non_zero_exit() -> None:
    backend = _make_backend()

    try:
        backend.exec_fg("exit 7", cwd=".")
        raise AssertionError("exec_fg should raise on non-zero exit")
    except RuntimeError as exc:
        assert "exit code 7" in str(exc)
