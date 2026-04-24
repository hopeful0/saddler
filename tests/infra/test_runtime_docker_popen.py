import struct
from collections import deque

import pytest
from docker.utils import socket as docker_socket

from saddler.infra.runtime.docker import DockerPopen


def _mux_frame(stream: int, payload: bytes) -> bytes:
    return struct.pack(">BxxxL", stream, len(payload)) + payload


class _FakeSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = deque(chunks)
        self.closed = False
        self.fd = 101

    def fileno(self) -> int:
        return self.fd

    def read_chunk(self, _size: int) -> bytes:
        if self._chunks:
            return self._chunks.popleft()
        return b""

    def close(self) -> None:
        self.closed = True


class _FakeApi:
    def __init__(self, sock: _FakeSocket) -> None:
        self.sock = sock
        self.exec_create_calls: list[dict[str, object]] = []
        self.exec_start_calls: list[dict[str, object]] = []

    def exec_create(
        self, container_id: str, command: list[str], **kwargs: object
    ) -> dict[str, str]:
        self.exec_create_calls.append(
            {"container_id": container_id, "command": command, **kwargs}
        )
        return {"Id": "exec-1"}

    def exec_start(self, exec_id: str, **kwargs: object) -> _FakeSocket:
        self.exec_start_calls.append({"exec_id": exec_id, **kwargs})
        return self.sock


@pytest.fixture(autouse=True)
def _patch_socket_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "saddler.infra.runtime.docker.select.select",
        lambda _r, _w, _x, _timeout=None: ([_r[0]], [], []),
    )
    monkeypatch.setattr(
        "saddler.infra.runtime.docker.docker_socket.read",
        lambda sock, size: sock.read_chunk(size),
    )


def test_spawn_parses_pid_handshake_success() -> None:
    sock = _FakeSocket(
        [
            _mux_frame(docker_socket.STDOUT, b"4321\n"),
            _mux_frame(docker_socket.STDOUT, b"payload"),
            b"",
        ]
    )
    api = _FakeApi(sock)

    popen = DockerPopen.spawn(
        api=api,
        container_id="cid-123",
        wrapped_command='echo $$; exec sh -lc "echo hi"',
        cwd="/workspace",
        env={"A": "1"},
        timeout=None,
        handshake_timeout=1.0,
        tty=False,
    )

    assert popen.pid == 4321
    assert api.exec_create_calls[0]["command"] == [
        "sh",
        "-lc",
        'echo $$; exec sh -lc "echo hi"',
    ]


def test_spawn_fails_when_pid_line_missing() -> None:
    sock = _FakeSocket([_mux_frame(docker_socket.STDOUT, b""), b""])
    api = _FakeApi(sock)

    with pytest.raises(RuntimeError, match="missing pid line"):
        DockerPopen.spawn(
            api=api,
            container_id="cid-123",
            wrapped_command='echo $$; exec sh -lc "echo hi"',
            cwd="/workspace",
            env=None,
            timeout=None,
            handshake_timeout=1.0,
            tty=False,
        )

    assert sock.closed is True


def test_spawn_fails_when_pid_is_not_integer() -> None:
    sock = _FakeSocket([_mux_frame(docker_socket.STDOUT, b"abc\n")])
    api = _FakeApi(sock)

    with pytest.raises(RuntimeError, match="non-integer pid"):
        DockerPopen.spawn(
            api=api,
            container_id="cid-123",
            wrapped_command='echo $$; exec sh -lc "echo hi"',
            cwd="/workspace",
            env=None,
            timeout=None,
            handshake_timeout=1.0,
            tty=False,
        )

    assert sock.closed is True


def test_spawn_fails_when_pid_handshake_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sock = _FakeSocket([])
    api = _FakeApi(sock)
    monkeypatch.setattr(
        "saddler.infra.runtime.docker.select.select",
        lambda _r, _w, _x, _timeout=None: ([], [], []),
    )

    with pytest.raises(RuntimeError, match="timed out"):
        DockerPopen.spawn(
            api=api,
            container_id="cid-123",
            wrapped_command='echo $$; exec sh -lc "echo hi"',
            cwd="/workspace",
            env=None,
            timeout=0.1,
            handshake_timeout=0.1,
            tty=False,
        )

    assert sock.closed is True
