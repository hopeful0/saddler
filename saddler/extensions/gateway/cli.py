from __future__ import annotations

import typer
import uvicorn

from .server.app import create_gateway_app

gateway_app = typer.Typer(help="Gateway server commands")


@gateway_app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8080, "--port", help="Bind port"),
) -> None:
    uvicorn.run(create_gateway_app(), host=host, port=port)


@gateway_app.command("sessions")
def sessions() -> None:
    typer.echo("Use gateway HTTP API to inspect active sessions.")
