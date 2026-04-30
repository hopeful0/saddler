from __future__ import annotations

import os
import secrets

import typer
import uvicorn

from .server.app import create_gateway_app

gateway_app = typer.Typer(help="Gateway server commands")


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
