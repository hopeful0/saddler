from __future__ import annotations

import asyncio
import io
import os
from types import SimpleNamespace

import pytest

from saddler.agent.model import Agent, AgentSpec, RoleSpec
from saddler.app.errors import NotFoundError
from saddler.extensions.gateway.app import tty_session as tty_session_mod
from saddler.extensions.gateway.app.tty_session import TtySession
from saddler.runtime.model import Runtime, RuntimeSpec


class _FakeHandle:
    def __init__(self) -> None:
        self._r, self._w = os.pipe()
        self.stdin = io.BytesIO()
        self.stdout = os.fdopen(self._r, "rb", buffering=0)
        self.stderr = None
        self.returncode: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def resize(self, rows: int, cols: int) -> None:
        _ = (rows, cols)

    def __enter__(self) -> _FakeHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        import contextlib

        with contextlib.suppress(OSError):
            os.close(self._w)
        with contextlib.suppress(OSError):
            self.stdout.close()


class _FakeHarness:
    def __init__(self) -> None:
        self.handle = _FakeHandle()

    @classmethod
    def from_spec(cls, spec: AgentSpec) -> _FakeHarness:
        _ = spec
        return cls()

    def tui(self, backend: object, *, tty: bool) -> _FakeHandle:
        _ = (backend, tty)
        return self.handle


class _FakeBackend:
    def __init__(self, running: bool) -> None:
        self._running = running

    def is_running(self) -> bool:
        return self._running

    @classmethod
    def load_state(cls, spec: RuntimeSpec, state: object) -> _FakeBackend:
        _ = (spec, state)
        return cls(running=True)


class _StoppedBackend(_FakeBackend):
    @classmethod
    def load_state(cls, spec: RuntimeSpec, state: object) -> _StoppedBackend:
        _ = (spec, state)
        return cls(running=False)


def _agent(agent_id: str = "a1") -> Agent:
    return Agent(
        id=agent_id,
        name="n",
        runtime="r1",
        spec=AgentSpec(
            harness="codex",
            workdir="/workspace",
            role=RoleSpec(name="role", source="local:///r", path=None),
            skills=[],
            rules=[],
            harness_spec=None,
        ),
    )


def _runtime() -> Runtime:
    return Runtime(
        id="r1",
        name="rt",
        spec=RuntimeSpec(backend_type="fake"),
        backend_state={},
    )


def test_tty_session_create_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def _gh(_name: str) -> type[_FakeHarness]:
        return _FakeHarness

    def _gb(_name: str) -> type[_FakeBackend]:
        return _FakeBackend

    monkeypatch.setattr(tty_session_mod, "get_harness_cls", _gh)
    monkeypatch.setattr(tty_session_mod, "get_runtime_backend_cls", _gb)

    api = SimpleNamespace(get_agent_runtime=lambda ref: (_agent(ref), _runtime()))

    async def _run() -> None:
        session = TtySession.create(api, "a1")  # type: ignore[arg-type]
        assert session.session_id
        await session.close()

    asyncio.run(_run())


def test_tty_session_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    def _gh(_name: str) -> type[_FakeHarness]:
        return _FakeHarness

    def _gb(_name: str) -> type[_StoppedBackend]:
        return _StoppedBackend

    monkeypatch.setattr(tty_session_mod, "get_harness_cls", _gh)
    monkeypatch.setattr(tty_session_mod, "get_runtime_backend_cls", _gb)

    api = SimpleNamespace(get_agent_runtime=lambda ref: (_agent(ref), _runtime()))
    with pytest.raises(NotFoundError):
        TtySession.create(api, "a1")  # type: ignore[arg-type]


def test_tty_session_agent_missing() -> None:
    api = SimpleNamespace(
        get_agent_runtime=lambda _ref: (_ for _ in ()).throw(NotFoundError("missing"))
    )
    with pytest.raises(NotFoundError):
        TtySession.create(api, "nope")  # type: ignore[arg-type]
