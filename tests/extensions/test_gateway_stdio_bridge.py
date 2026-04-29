from __future__ import annotations

import asyncio
import io

from saddler.extensions.gateway.bridge.stdio import ACPStdioBridge


class _FakeHandle:
    def __init__(self, stdout_data: bytes = b"") -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(stdout_data)
        self.stderr = io.BytesIO()
        self.returncode: int | None = None
        self.terminated = False
        self.waited = False

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        self.waited = True
        self.returncode = 0
        return 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.returncode = -9

    def resize(self, rows: int, cols: int) -> None:
        _ = (rows, cols)

    def __enter__(self) -> _FakeHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        _ = (exc_type, exc, tb)


def test_stdio_bridge_send_recv_and_close() -> None:
    handle = _FakeHandle(b'{"type":"event","data":"world"}\n')
    bridge = ACPStdioBridge(handle)

    async def _run() -> None:
        await bridge.send({"type": "run", "content": "hello"})
        got = await bridge.recv()
        assert got == {"type": "event", "data": "world"}
        await bridge.close()

    asyncio.run(_run())
    assert handle.stdin.getvalue() == b'{"type": "run", "content": "hello"}\n'
    assert handle.terminated is True
    assert handle.waited is True


def test_stdio_bridge_recv_eof_raises() -> None:
    bridge = ACPStdioBridge(_FakeHandle(b""))

    async def _run() -> None:
        try:
            await bridge.recv()
        except EOFError:
            return
        raise AssertionError("expected EOFError")

    asyncio.run(_run())
