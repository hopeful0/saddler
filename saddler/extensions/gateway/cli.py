from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sys
from typing import Literal

import typer
import uvicorn

from .server.app import create_gateway_app

gateway_app = typer.Typer(help="Gateway server and remote client commands")


def _resolve_token(auth_token: str | None) -> str:
    if auth_token is not None:
        return auth_token
    env_token = os.environ.get("SADDLER_GATEWAY_TOKEN")
    if env_token:
        return env_token
    return secrets.token_urlsafe(32)


@gateway_app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8080, "--port", help="Bind port"),
    auth_token: str | None = typer.Option(
        None,
        "--auth-token",
        help="Gateway auth token (defaults to env or generated secret)",
    ),
) -> None:
    token = _resolve_token(auth_token)
    address = f"http://{host}:{port}"
    typer.echo(f"Address: {address}", err=True)
    typer.echo(f"Browser: {address}/login?token={token}", err=True)
    typer.echo(f"TTY UI: {address}/tui/", err=True)
    typer.echo(f"API key: {token}", err=True)
    uvicorn.run(create_gateway_app(token=token), host=host, port=port)


@gateway_app.command("sessions")
def sessions() -> None:
    typer.echo("Use gateway HTTP API to inspect active sessions.")


def _resolve_connect_token(token: str | None) -> str:
    if token:
        return token
    env = os.environ.get("SADDLER_GATEWAY_TOKEN")
    if env:
        return env
    typer.echo(
        "Missing auth token: pass --token or set SADDLER_GATEWAY_TOKEN.",
        err=True,
    )
    raise typer.Exit(1)


async def _connect_stdin_loop(
    loop: asyncio.AbstractEventLoop,
    session: object,
) -> None:
    reader: asyncio.StreamReader | None = None
    transport: asyncio.Transport | None = None
    try:
        if hasattr(sys.stdin, "fileno") and sys.stdin.isatty():
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    except (NotImplementedError, OSError, ValueError):
        # Fallback for test harnesses or non-file stdin objects.
        reader = None
        transport = None

    try:
        if reader is not None:
            while True:
                line = await reader.readline()
                if line == b"":
                    return
                stripped = line.decode("utf-8").strip()
                if not stripped:
                    continue
                await session.send(json.loads(stripped))
            return

        while True:
            line: str = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":
                return
            stripped = line.strip()
            if not stripped:
                continue
            await session.send(json.loads(stripped))
    finally:
        if transport is not None:
            transport.close()


async def _connect_recv_loop(session: object) -> None:
    try:
        while True:
            msg = await session.recv()
            typer.echo(json.dumps(msg, ensure_ascii=False))
    except EOFError:
        return


async def _connect_async(
    url: str,
    agent_ref: str,
    transport: Literal["ws", "http"],
    token: str,
) -> None:
    from .client import GatewayClient

    client = GatewayClient(url, token)
    session = await client.connect(agent_ref, transport=transport)
    loop = asyncio.get_running_loop()
    recv_task = asyncio.create_task(_connect_recv_loop(session))
    stdin_task = asyncio.create_task(_connect_stdin_loop(loop, session))
    try:
        _, pending = await asyncio.wait(
            {recv_task, stdin_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(recv_task, stdin_task, return_exceptions=True)
    finally:
        await session.close()


async def _connect_tty_async(url: str, agent_ref: str, token: str) -> None:
    import shutil
    import signal

    try:
        import termios
        import tty
    except ImportError:
        typer.echo(
            "saddler gateway connect --tui requires POSIX termios (Unix-like OS).",
            err=True,
        )
        raise typer.Exit(1) from None

    if not sys.stdin.isatty():
        typer.echo(
            "saddler gateway connect --tui requires an interactive TTY stdin.",
            err=True,
        )
        raise typer.Exit(1)

    import websockets

    from .client.ws import gateway_agent_tty_uri

    uri = gateway_agent_tty_uri(url, agent_ref)
    headers = [("Authorization", f"Bearer {token}")]
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    old = termios.tcgetattr(stdin_fd)
    loop = asyncio.get_running_loop()
    sig_registered = False

    async def _send_resize(ws: object) -> None:
        size = shutil.get_terminal_size()
        payload = json.dumps(
            {"type": "resize", "cols": size.columns, "rows": size.lines}
        )
        await ws.send(payload)

    async with websockets.connect(uri, additional_headers=headers) as ws:
        tty.setraw(stdin_fd)
        try:
            winch = asyncio.Event()

            def _on_winch(*_: object) -> None:
                loop.call_soon_threadsafe(winch.set)

            if hasattr(signal, "SIGWINCH"):
                try:
                    loop.add_signal_handler(signal.SIGWINCH, _on_winch)
                    sig_registered = True
                except (NotImplementedError, RuntimeError):
                    sig_registered = False

            await _send_resize(ws)

            async def winch_loop() -> None:
                while True:
                    await winch.wait()
                    winch.clear()
                    await _send_resize(ws)

            winch_task = asyncio.create_task(winch_loop()) if sig_registered else None

            async def stdin_to_ws() -> None:
                while True:
                    data = await loop.run_in_executor(None, os.read, stdin_fd, 4096)
                    if data == b"":
                        await ws.close()
                        return
                    await ws.send(data)

            async def ws_to_stdout() -> None:
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        await loop.run_in_executor(None, os.write, stdout_fd, msg)

            stdin_task = asyncio.create_task(stdin_to_ws())
            stdout_task = asyncio.create_task(ws_to_stdout())
            wait_on: set[asyncio.Task[object]] = {stdin_task, stdout_task}
            if winch_task is not None:
                wait_on.add(winch_task)
            try:
                done, pending = await asyncio.wait(
                    wait_on,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for t in done:
                    with contextlib.suppress(asyncio.CancelledError):
                        await t
            finally:
                if winch_task is not None:
                    winch_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await winch_task
        finally:
            if sig_registered:
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(signal.SIGWINCH)
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old)


@gateway_app.command("connect")
def connect(
    url: str = typer.Argument(help="Gateway base URL (http:// or https://)."),
    agent_ref: str = typer.Argument(help="Agent id to attach to."),
    transport: str = typer.Option(
        "ws",
        "--transport",
        help="Transport: ws (WebSocket) or http (Streamable HTTP + SSE).",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help="Bearer token (defaults to SADDLER_GATEWAY_TOKEN).",
    ),
    tui: bool = typer.Option(False, "--tui"),
) -> None:
    if transport not in {"ws", "http"}:
        typer.echo("--transport must be ws or http", err=True)
        raise typer.Exit(1)
    if tui and transport != "ws":
        typer.echo("--tui requires --transport ws", err=True)
        raise typer.Exit(1)
    auth = _resolve_connect_token(token)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        if tui:
            asyncio.run(_connect_tty_async(url, agent_ref, auth))
        else:
            asyncio.run(
                _connect_async(
                    url,
                    agent_ref,
                    transport=transport,  # type: ignore[arg-type]
                    token=auth,
                )
            )
    except KeyboardInterrupt:
        raise typer.Exit(130) from None
