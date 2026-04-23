"""saddler hitch — compose-style multi-agent orchestration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

import typer
import yaml

from ..app import build_use_cases
from .errors import HitchValidationError
from .executor import HITCH_SERVICE, HitchExecutor
from .loader import load_config, validate_dag
from .plan import build_plan

hitch_app = typer.Typer(
    name="hitch",
    help="Compose-style multi-agent orchestration (like docker compose for agents)",
    no_args_is_help=True,
)

FilesArg = Annotated[
    list[Path] | None,
    typer.Option(
        "-f",
        "--file",
        help="Hitch config file(s); may be repeated. Default: hitch.yaml / hitch.yml / saddler.yaml",
        exists=True,
        dir_okay=False,
    ),
]

ProjectOpt = Annotated[
    str | None,
    typer.Option(
        "--project",
        "-p",
        help="Project name. When given, skips config file lookup.",
    ),
]


@lru_cache(maxsize=1)
def _executor() -> HitchExecutor:
    return HitchExecutor(build_use_cases())


def _load(files: list[Path] | None, cwd: Path):
    """Resolve files → load → validate DAG. Returns (config, project, compose_file)."""
    config, project, compose_file = load_config(files or [], cwd)
    validate_dag(config)
    return config, project, compose_file


def _resolve_project(
    project: str | None,
    files: list[Path] | None,
    cwd: Path,
) -> str:
    """Return project name either from --project or by loading the config file."""
    if project is not None:
        return project
    _, p, _ = _load(files, cwd)
    return p


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@hitch_app.command("config")
def cmd_config(
    files: FilesArg = None,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Only validate, no output")
    ] = False,
) -> None:
    """Validate and print the effective hitch configuration."""
    try:
        config, project, compose_file = _load(files, Path.cwd())
    except HitchValidationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not quiet:
        typer.echo(f"project: {project}")
        typer.echo(f"compose_file: {compose_file}")
        typer.echo(
            yaml.dump(
                config.model_dump(exclude_none=True), default_flow_style=False
            ).rstrip()
        )


# ---------------------------------------------------------------------------
# up
# ---------------------------------------------------------------------------


@hitch_app.command("up")
def cmd_up(
    files: FilesArg = None,
    force_recreate: Annotated[
        bool,
        typer.Option(
            "--force-recreate",
            help="Remove and recreate all hitch-owned resources for this project",
        ),
    ] = False,
) -> None:
    """Create and start all declared storages, runtimes, and agents."""
    try:
        config, project, compose_file = _load(files, Path.cwd())
    except HitchValidationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    plan = build_plan(config, compose_file, project)

    try:
        _executor().up(plan, force_recreate=force_recreate)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"hitch up complete for project '{project}'")


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@hitch_app.command("stop")
def cmd_stop(
    files: FilesArg = None,
    project: ProjectOpt = None,
) -> None:
    """Stop runtimes belonging to this project (keeps records intact)."""
    try:
        proj = _resolve_project(project, files, Path.cwd())
    except HitchValidationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    try:
        _executor().stop(proj)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"hitch stop complete for project '{proj}'")


# ---------------------------------------------------------------------------
# down
# ---------------------------------------------------------------------------


@hitch_app.command("down")
def cmd_down(
    files: FilesArg = None,
    project: ProjectOpt = None,
) -> None:
    """Stop and remove all hitch-owned agents, runtimes, and storages."""
    try:
        proj = _resolve_project(project, files, Path.cwd())
    except HitchValidationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    try:
        _executor().down(proj)
    except RuntimeError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"hitch down complete for project '{proj}'")


# ---------------------------------------------------------------------------
# ps
# ---------------------------------------------------------------------------


@hitch_app.command("ps")
def cmd_ps(
    files: FilesArg = None,
    project: ProjectOpt = None,
) -> None:
    """List agents and runtimes belonging to this project."""
    try:
        proj = _resolve_project(project, files, Path.cwd())
    except HitchValidationError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    result = _executor().ps(proj)
    storages = result["storages"]
    runtimes_with_status: list[tuple] = result["runtimes"]
    agents = result["agents"]

    typer.echo(f"Project: {proj}\n")

    if storages:
        typer.echo("STORAGES")
        typer.echo(f"  {'ID':<36}  {'SERVICE':<20}  TYPE")
        for s in storages:
            svc = (s.metadata or {}).get(HITCH_SERVICE, "")
            typer.echo(f"  {s.id:<36}  {svc:<20}  {s.spec.type}")
        typer.echo("")

    if runtimes_with_status:
        typer.echo("RUNTIMES")
        typer.echo(f"  {'ID':<36}  {'SERVICE':<20}  {'BACKEND':<10}  STATUS")
        for r, status in runtimes_with_status:
            svc = (r.metadata or {}).get(HITCH_SERVICE, "")
            typer.echo(f"  {r.id:<36}  {svc:<20}  {r.spec.backend_type:<10}  {status}")
        typer.echo("")

    if agents:
        typer.echo("AGENTS")
        typer.echo(f"  {'ID':<36}  {'SERVICE':<20}  HARNESS")
        for a in agents:
            svc = (a.metadata or {}).get(HITCH_SERVICE, "")
            typer.echo(f"  {a.id:<36}  {svc:<20}  {a.spec.harness}")
        typer.echo("")

    if not storages and not runtimes_with_status and not agents:
        typer.echo(f"No resources found for project '{proj}'")
