from __future__ import annotations

import asyncio
import json
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
    while True:
        line: str = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":
            return
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        await session.send(payload)


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
) -> None:
    if transport not in {"ws", "http"}:
        typer.echo("--transport must be ws or http", err=True)
        raise typer.Exit(1)
    auth = _resolve_connect_token(token)
    try:
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
