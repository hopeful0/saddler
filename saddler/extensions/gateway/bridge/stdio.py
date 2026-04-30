from __future__ import annotations

import asyncio
import json

from ....runtime.backend import ProcessHandle


class ACPStdioBridge:
    def __init__(self, handle: ProcessHandle) -> None:
        self._handle = handle

    async def send(self, msg: dict) -> None:
        payload = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        loop = asyncio.get_running_loop()

        def _write() -> None:
            if self._handle.stdin is None:
                raise RuntimeError("process stdin is unavailable")
            self._handle.stdin.write(payload)
            self._handle.stdin.flush()

        await loop.run_in_executor(None, _write)

    async def recv(self) -> dict:
        loop = asyncio.get_running_loop()

        def _readline() -> bytes:
            if self._handle.stdout is None:
                raise RuntimeError("process stdout is unavailable")
            return self._handle.stdout.readline()

        line = await loop.run_in_executor(None, _readline)
        if line == b"":
            raise EOFError("process stdout reached EOF")
        return json.loads(line.decode("utf-8"))

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._handle.__exit__, None, None, None)
