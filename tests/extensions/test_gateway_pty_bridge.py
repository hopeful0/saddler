from __future__ import annotations

import asyncio
import contextlib
import errno
import io
import os

import pytest

from saddler.extensions.gateway.bridge.pty import PtyBridge


class _PipeHandle:
    def __init__(self, stdout_read: int, stdout_write: int) -> None:
        self._stdout_write = stdout_write
        self.stdin = io.BytesIO()
        self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
        self.stderr = None
        self.returncode: int | None = None
        self.resized: list[tuple[int, int]] = []
        self._exited = False

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.returncode = 0
        return 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def resize(self, rows: int, cols: int) -> None:
        self.resized.append((rows, cols))

    def __enter__(self) -> _PipeHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._exited = True
        with contextlib.suppress(OSError):
            os.close(self._stdout_write)
        with contextlib.suppress(OSError):
            self.stdout.close()


def test_pty_bridge_read_write_resize_close() -> None:
    out_r, out_w = os.pipe()
    handle = _PipeHandle(out_r, out_w)

    async def _run() -> None:
        bridge = PtyBridge(handle)
        os.write(out_w, b"hello")
        await asyncio.sleep(0)
        got = await bridge.read()
        assert got == b"hello"
        await bridge.write(b"xyz")
        bridge.resize(24, 80)
        assert handle.resized == [(24, 80)]
        assert handle.stdin.getvalue() == b"xyz"
        os.close(out_w)
        with pytest.raises(EOFError):
            await bridge.read()
        await bridge.close()

    asyncio.run(_run())
    assert handle._exited is True


def test_pty_bridge_eio_feeds_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    out_r, out_w = os.pipe()
    handle = _PipeHandle(out_r, out_w)
    handle.returncode = 0

    def _fake_read(_fd: int, _n: int) -> bytes:
        raise OSError(errno.EIO, "simulated")

    monkeypatch.setattr("saddler.extensions.gateway.bridge.pty.os.read", _fake_read)

    async def _run() -> None:
        bridge = PtyBridge(handle)
        os.write(out_w, b"\0")
        await asyncio.sleep(0)
        with pytest.raises(EOFError):
            await bridge.read()
        await bridge.close()

    asyncio.run(_run())
    with contextlib.suppress(OSError):
        os.close(out_w)
