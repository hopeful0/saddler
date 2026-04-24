import shlex

import pytest

from saddler.runtime.backend import normalize_shell_command


def test_normalize_shell_command_string_passthrough() -> None:
    command = "echo hello"
    assert normalize_shell_command(command) == command


def test_normalize_shell_command_list_uses_shlex_join() -> None:
    command = ["echo", "hello world", "$HOME"]
    assert normalize_shell_command(command) == shlex.join(command)


@pytest.mark.parametrize("command", ["", "   ", []])
def test_normalize_shell_command_empty_raises_value_error(
    command: str | list[str],
) -> None:
    with pytest.raises(ValueError, match="command must not be empty"):
        normalize_shell_command(command)


def test_normalize_shell_command_non_string_item_raises_type_error() -> None:
    command = ["echo", "ok", 1]
    with pytest.raises(TypeError, match="command list items must be str"):
        normalize_shell_command(command)  # type: ignore[arg-type]
