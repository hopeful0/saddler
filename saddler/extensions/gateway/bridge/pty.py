from __future__ import annotations

import asyncio
import contextlib
import errno
import os

from ....runtime.backend import ProcessHandle


class PtyBridge:
    def __init__(self, handle: ProcessHandle) -> None:
        self._handle = handle
        self._reader = asyncio.StreamReader()
        if handle.stdout is None:
            msg = "PTY bridge requires stdout"
            raise RuntimeError(msg)
        self._fd = handle.stdout.fileno()
        self._loop = asyncio.get_running_loop()
        self._reader_added = True
        self._loop.add_reader(self._fd, self._on_readable)

    def _detach_reader(self) -> None:
        if not self._reader_added:
            return
        self._reader_added = False
        with contextlib.suppress(Exception):
            self._loop.remove_reader(self._fd)

    def _on_readable(self) -> None:
        try:
            chunk = os.read(self._fd, 65536)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            self._detach_reader()
            self._reader.feed_eof()
            return
        if chunk:
            self._reader.feed_data(chunk)
        else:
            self._detach_reader()
            self._reader.feed_eof()

    async def read(self) -> bytes:
        data = await self._reader.read(65536)
        if data == b"" and self._reader.at_eof():
            raise EOFError("pty stdout closed")
        return data

    async def write(self, data: bytes) -> None:
        loop = asyncio.get_running_loop()

        def _write() -> None:
            if self._handle.stdin is None:
                raise RuntimeError("process stdin is unavailable")
            self._handle.stdin.write(data)
            self._handle.stdin.flush()

        await loop.run_in_executor(None, _write)

    def resize(self, rows: int, cols: int) -> None:
        self._handle.resize(rows, cols)

    async def close(self) -> None:
        self._detach_reader()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._shutdown_sync)

    def _shutdown_sync(self) -> None:
        self._handle.terminate()
        self._handle.__exit__(None, None, None)

    async def __aenter__(self) -> PtyBridge:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()
