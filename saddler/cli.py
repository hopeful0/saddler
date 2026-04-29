"""saddler CLI entry point."""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path, PurePosixPath

import typer
import yaml
from rich.markup import escape as rich_escape

# Register all infra adapters (backends, harnesses, fetchers).
import saddler.infra as _infra  # noqa: F401

from .agent.harness import AGENT_HARNESS_REGISTRY, get_harness_cls
from .agent.model import AgentSpec
from .app import UseCases, build_use_cases
from .app.errors import AppError
from .api.agent import AgentApiService, AgentCreateRequest, ResourceCreateSpec
from .api.runtime import MountSpec, RuntimeApiService, RuntimeCreateRequest
from .api.storage import StorageApiService, StorageCreateRequest
from .runtime.backend import get_runtime_backend_cls
from .runtime.backend import RUNTIME_BACKEND_REGISTRY
from .hitch.cli import hitch_app

try:
    from .extensions.gateway.cli import gateway_app
except ImportError:
    gateway_app = None


def _help(text: str) -> str:
    """Escape Rich markup control chars in help text."""
    return rich_escape(text)


def _saddler_version() -> str:
    try:
        return package_version("saddler")
    except PackageNotFoundError:
        return "unknown"


def _version_callback(show_version: bool) -> None:
    if not show_version:
        return
    typer.echo(_saddler_version())
    raise typer.Exit()


app = typer.Typer(
    name="saddler",
    help=_help("Agent harness assembler and lifecycle manager"),
    rich_markup_mode="rich",
)
agent_app = typer.Typer(name="agent", help=_help("Agent lifecycle commands"))
runtime_app = typer.Typer(name="runtime", help=_help("Runtime lifecycle commands"))
runtime_create_app = typer.Typer(
    name="create",
    help=_help("Create runtime environments"),
    invoke_without_command=True,
)
storage_app = typer.Typer(name="storage", help=_help("Storage lifecycle commands"))

app.add_typer(agent_app, name="")
app.add_typer(agent_app, name="agent")
app.add_typer(runtime_app, name="runtime")
app.add_typer(runtime_app, name="rt")
runtime_app.add_typer(runtime_create_app, name="create")
app.add_typer(storage_app, name="storage")
app.add_typer(storage_app, name="st")
app.add_typer(hitch_app, name="hitch")
if gateway_app is not None:
    app.add_typer(gateway_app, name="gateway")


# ---------------------------------------------------------------------------
# Shared state (lazy init)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _use_cases() -> UseCases:
    return build_use_cases()


@lru_cache(maxsize=1)
def _storage_api() -> StorageApiService:
    return StorageApiService(_use_cases().storage)


@lru_cache(maxsize=1)
def _runtime_api() -> RuntimeApiService:
    return RuntimeApiService(_use_cases().runtime, _use_cases().storage)


@lru_cache(maxsize=1)
def _agent_api() -> AgentApiService:
    return AgentApiService(_use_cases().agent)


# ---------------------------------------------------------------------------
# Logging / global flags
# ---------------------------------------------------------------------------


@app.callback()
def app_callback(
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Warnings/errors only"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logs"),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show saddler version and exit",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    _ = version
    if quiet and verbose:
        raise typer.BadParameter("--quiet and --verbose are mutually exclusive")
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_metadata(meta: list[str] | None) -> dict[str, str] | None:
    if not meta:
        return None
    out: dict[str, str] = {}
    for raw in meta:
        if "=" not in raw:
            raise typer.BadParameter(
                f"Invalid --meta format: {raw!r} (expected KEY=VALUE)"
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"Invalid --meta format: {raw!r} (empty key)")
        out[key] = value
    return out or None


def _parse_env(entries: list[str] | None) -> dict[str, str]:
    if not entries:
        return {}
    out: dict[str, str] = {}
    for raw in entries:
        if "=" not in raw:
            raise typer.BadParameter(
                f"Invalid --env format: {raw!r} (expected KEY=VALUE)"
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"Invalid --env format: {raw!r} (empty key)")
        out[key] = value
    return out


def _parse_opt(entries: list[str] | None) -> dict[str, str]:
    if not entries:
        return {}
    out: dict[str, str] = {}
    for raw in entries:
        if "=" not in raw:
            raise typer.BadParameter(
                f"Invalid --opt format: {raw!r} (expected KEY=VALUE)"
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter(f"Invalid --opt format: {raw!r} (empty key)")
        out[key] = value
    return out


def _parse_storage_mount(entry: str) -> MountSpec:
    """Parse `<storage-ref>:<dest>[:<mode>]`."""
    parts = entry.split(":")
    if len(parts) < 2:
        raise typer.BadParameter(
            f"Invalid --storage format: {entry!r} (expected <ref>:<dest>[:<mode>])"
        )
    ref, dest = parts[0].strip(), parts[1].strip()
    mode = parts[2].strip() if len(parts) > 2 else "rw"
    if not ref or not dest:
        raise typer.BadParameter(f"Invalid --storage format: {entry!r}")
    return MountSpec(type="storage", destination=dest, storage_ref=ref, mode=mode)


def _parse_bind_mount(entry: str) -> MountSpec:
    """Parse `<host-path>:<dest>[:<mode>]`."""
    parts = entry.split(":")
    if len(parts) < 2:
        raise typer.BadParameter(
            f"Invalid --mount format: {entry!r} (expected <host>:<dest>[:<mode>])"
        )
    host, dest = parts[0].strip(), parts[1].strip()
    mode = parts[2].strip() if len(parts) > 2 else "rw"
    if not host or not dest:
        raise typer.BadParameter(f"Invalid --mount format: {entry!r}")
    if not PurePosixPath(dest).is_absolute():
        raise typer.BadParameter(f"--mount destination must be absolute: {dest!r}")
    host_resolved = str(Path(host).expanduser().resolve())
    if not Path(host_resolved).exists():
        raise typer.BadParameter(f"--mount host path does not exist: {host_resolved!r}")
    return MountSpec(type="bind", destination=dest, source=host_resolved, mode=mode)


def _parse_resource(entry: str, default_name: str | None = None) -> ResourceCreateSpec:
    """Parse `[name@]source` into a ResourceCreateSpec."""
    if "@" in entry:
        name, source = entry.split("@", 1)
        name = name.strip()
        source = source.strip()
    else:
        source = entry.strip()
        name = default_name or Path(source).stem
    if not name or not source:
        raise typer.BadParameter(f"Invalid resource format: {entry!r}")
    return ResourceCreateSpec(name=name, source=source)


def _validate_harness(value: str) -> str:
    available = AGENT_HARNESS_REGISTRY.list()
    if value not in available:
        available_text = ", ".join(available) if available else "(none)"
        raise typer.BadParameter(
            f"Unknown harness: {value!r}. Available: {available_text}"
        )
    return value


def _complete_harness(incomplete: str) -> list[str]:
    return [
        name for name in AGENT_HARNESS_REGISTRY.list() if name.startswith(incomplete)
    ]


def _complete_prefix(candidates: list[str], incomplete: str) -> list[str]:
    return [item for item in candidates if item.startswith(incomplete)]


def _runtime_refs() -> list[str]:
    refs: list[str] = []
    for runtime in _runtime_api().list():
        refs.append(runtime.id)
        if runtime.name:
            refs.append(runtime.name)
    return sorted(set(refs))


def _agent_refs() -> list[str]:
    refs: list[str] = []
    for agent in _agent_api().list():
        refs.append(agent.id)
        if agent.name:
            refs.append(agent.name)
    return sorted(set(refs))


def _storage_refs() -> list[str]:
    refs: list[str] = []
    for storage in _storage_api().list():
        refs.append(storage.id)
        if storage.name:
            refs.append(storage.name)
    return sorted(set(refs))


def _complete_runtime_ref(incomplete: str) -> list[str]:
    try:
        return _complete_prefix(_runtime_refs(), incomplete)
    except Exception:
        return []


def _complete_agent_ref(incomplete: str) -> list[str]:
    try:
        return _complete_prefix(_agent_refs(), incomplete)
    except Exception:
        return []


def _complete_storage_ref(incomplete: str) -> list[str]:
    try:
        return _complete_prefix(_storage_refs(), incomplete)
    except Exception:
        return []


def _complete_backend_type(incomplete: str) -> list[str]:
    try:
        return _complete_prefix(RUNTIME_BACKEND_REGISTRY.list(), incomplete)
    except Exception:
        return []


def _complete_storage_type(incomplete: str) -> list[str]:
    return _complete_prefix(["local", "nfs"], incomplete)


def _echo_runtime_created(rec: object) -> None:
    runtime_id = getattr(rec, "id")
    runtime_name = getattr(rec, "name")
    label = runtime_name or runtime_id
    typer.echo(f"Created runtime {label} ({runtime_id})")
    typer.echo(f"  Next: saddler runtime start {label}", err=False)


def _runtime_create_common(
    *,
    backend_type: str,
    env_entries: list[str] | None,
    storage: list[str] | None,
    mount: list[str] | None,
    name: str | None,
    meta: list[str] | None,
    opt: list[str] | None,
    image: str | None = None,
    user: str | None = None,
) -> None:
    mounts: list[MountSpec] = []
    for s in storage or []:
        mounts.append(_parse_storage_mount(s))
    for m in mount or []:
        mounts.append(_parse_bind_mount(m))

    backend_spec = _parse_opt(opt)
    if image:
        backend_spec["image"] = image
    if user:
        backend_spec["user"] = user

    rec = _runtime_api().create(
        RuntimeCreateRequest(
            backend_type=backend_type,
            env=_parse_env(env_entries),
            mounts=mounts,
            backend_spec=backend_spec or None,
            name=name,
            metadata=_parse_metadata(meta),
        )
    )
    _echo_runtime_created(rec)


# ---------------------------------------------------------------------------
# Storage commands
# ---------------------------------------------------------------------------


@storage_app.command("create")
def storage_create(
    storage_type: str = typer.Option(
        ...,
        "--type",
        "-t",
        help="Storage type: local | nfs",
        autocompletion=_complete_storage_type,
    ),
    path: str | None = typer.Option(None, "--path", "-p", help="Host path (local/nfs)"),
    server: str | None = typer.Option(None, "--server", help="NFS server address"),
    name: str | None = typer.Option(None, "--name", "-n", help="Display name"),
    meta: list[str] | None = typer.Option(
        None, "--meta", "-m", help="KEY=VALUE metadata (repeatable)"
    ),
) -> None:
    """Create a storage resource."""
    rec = _storage_api().create(
        StorageCreateRequest(
            type=storage_type,
            path=path,
            server=server,
            name=name,
            metadata=_parse_metadata(meta),
        )
    )
    typer.echo(f"Created storage {rec.name or rec.id} ({rec.id})")


@storage_app.command("rm")
def storage_rm(
    ref: str = typer.Argument(
        ..., help="Storage name or ID", autocompletion=_complete_storage_ref
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Remove even if mounted"),
) -> None:
    """Remove a storage resource."""
    _storage_api().remove(ref, force=force)
    typer.echo(f"Removed storage {ref}.")


@storage_app.command("ls")
def storage_ls() -> None:
    """List all storages."""
    records = _storage_api().list()
    if not records:
        typer.echo("No storages found.")
        return
    typer.echo(f"{'ID':<34} {'NAME':<20} {'TYPE':<8} {'MOUNTED':>7}")
    typer.echo("-" * 72)
    for r in records:
        typer.echo(
            f"{r.id:<34} {(r.name or '-'):<20} {r.spec.type:<8} {len(r.mounted_by):>7}"
        )


@storage_app.command("inspect")
def storage_inspect(
    ref: str = typer.Argument(
        ..., help="Storage name or ID", autocompletion=_complete_storage_ref
    ),
) -> None:
    """Show full details of a storage."""
    rec = _storage_api().inspect(ref)
    typer.echo(
        yaml.safe_dump(
            rec.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        ).strip()
    )


# ---------------------------------------------------------------------------
# Runtime commands
# ---------------------------------------------------------------------------


@runtime_create_app.callback(invoke_without_command=True)
def runtime_create(
    ctx: typer.Context,
    backend_type: str | None = typer.Option(
        None,
        "--type",
        "-t",
        help="Runtime type for generic path",
        autocompletion=_complete_backend_type,
    ),
    env_entries: list[str] | None = typer.Option(
        None, "--env", "-e", help="KEY=VALUE env (repeatable)"
    ),
    storage: list[str] | None = typer.Option(
        None, "--storage", "-S", help=_help("<ref>:<dest>[:<mode>] (repeatable)")
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Display name"),
    meta: list[str] | None = typer.Option(
        None, "--meta", "-m", help="KEY=VALUE metadata (repeatable)"
    ),
    opt: list[str] | None = typer.Option(
        None,
        "--opt",
        help=_help("KEY=VALUE backend option (repeatable; later entries override)"),
    ),
) -> None:
    """Create a runtime environment (generic dynamic path)."""
    if ctx.invoked_subcommand:
        return
    if not backend_type:
        raise typer.BadParameter(
            "Missing backend type. Use `saddler runtime create --type <name>` "
            "or `saddler runtime create local|docker`."
        )

    _runtime_create_common(
        backend_type=backend_type,
        env_entries=env_entries,
        storage=storage,
        mount=None,
        name=name,
        meta=meta,
        opt=opt,
    )


@runtime_create_app.command("local")
def runtime_create_local(
    env_entries: list[str] | None = typer.Option(
        None, "--env", "-e", help="KEY=VALUE env (repeatable)"
    ),
    storage: list[str] | None = typer.Option(
        None, "--storage", "-S", help=_help("<ref>:<dest>[:<mode>] (repeatable)")
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Display name"),
    meta: list[str] | None = typer.Option(
        None, "--meta", "-m", help="KEY=VALUE metadata (repeatable)"
    ),
    opt: list[str] | None = typer.Option(
        None,
        "--opt",
        help=_help("KEY=VALUE backend option (repeatable; later entries override)"),
    ),
) -> None:
    """Create a local runtime."""
    _runtime_create_common(
        backend_type="local",
        env_entries=env_entries,
        storage=storage,
        mount=None,
        name=name,
        meta=meta,
        opt=opt,
    )


@runtime_create_app.command("docker")
def runtime_create_docker(
    image: str | None = typer.Option(None, "--image", "-i", help="Docker image"),
    user: str | None = typer.Option(None, "--user", help="Run as user"),
    env_entries: list[str] | None = typer.Option(
        None, "--env", "-e", help="KEY=VALUE env (repeatable)"
    ),
    storage: list[str] | None = typer.Option(
        None, "--storage", "-S", help=_help("<ref>:<dest>[:<mode>] (repeatable)")
    ),
    mount: list[str] | None = typer.Option(
        None,
        "--mount",
        "-M",
        help=_help("<host>:<dest>[:<mode>] bind mount (repeatable)"),
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Display name"),
    meta: list[str] | None = typer.Option(
        None, "--meta", "-m", help="KEY=VALUE metadata (repeatable)"
    ),
    opt: list[str] | None = typer.Option(
        None,
        "--opt",
        help=_help("KEY=VALUE backend option (repeatable; later entries override)"),
    ),
) -> None:
    """Create a docker runtime."""
    _runtime_create_common(
        backend_type="docker",
        env_entries=env_entries,
        storage=storage,
        mount=mount,
        name=name,
        meta=meta,
        opt=opt,
        image=image,
        user=user,
    )


@runtime_app.command("start")
def runtime_start(
    ref: str = typer.Argument(
        ..., help="Runtime name or ID", autocompletion=_complete_runtime_ref
    ),
) -> None:
    """Start a runtime."""
    _runtime_api().start(ref)
    typer.echo(f"Started runtime {ref}.")


@runtime_app.command("stop")
def runtime_stop(
    ref: str = typer.Argument(
        ..., help="Runtime name or ID", autocompletion=_complete_runtime_ref
    ),
) -> None:
    """Stop a runtime."""
    _runtime_api().stop(ref)
    typer.echo(f"Stopped runtime {ref}.")


@runtime_app.command("rm")
def runtime_rm(
    ref: str = typer.Argument(
        ..., help="Runtime name or ID", autocompletion=_complete_runtime_ref
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Remove even if agents are using it"
    ),
) -> None:
    """Remove a runtime."""
    _runtime_api().remove(ref, force=force)
    typer.echo(f"Removed runtime {ref}.")


@runtime_app.command("ls")
def runtime_ls() -> None:
    """List all runtimes."""
    entries = _runtime_api().list_with_status()
    if not entries:
        typer.echo("No runtimes found.")
        return
    typer.echo(f"{'ID':<34} {'NAME':<20} {'TYPE':<8} {'STATUS':<12} {'AGENTS':>6}")
    typer.echo("-" * 88)
    for r, status in entries:
        typer.echo(
            f"{r.id:<34} {(r.name or '-'):<20} {r.spec.backend_type:<8} {status:<12} {len(r.used_by):>6}"
        )


@runtime_app.command("inspect")
def runtime_inspect(
    ref: str = typer.Argument(
        ..., help="Runtime name or ID", autocompletion=_complete_runtime_ref
    ),
) -> None:
    """Show full details of a runtime."""
    rec = _runtime_api().inspect(ref)
    typer.echo(
        yaml.safe_dump(
            rec.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        ).strip()
    )


@runtime_app.command("harnesses")
def runtime_harnesses(
    ref: str = typer.Argument(
        ..., help="Runtime name or ID", autocompletion=_complete_runtime_ref
    ),
) -> None:
    """List harness install status on a runtime."""
    runtime = _runtime_api().inspect(ref)
    backend = get_runtime_backend_cls(runtime.spec.backend_type).load_state(
        runtime.spec, runtime.backend_state
    )
    harnesses = AGENT_HARNESS_REGISTRY.list()
    if not harnesses:
        typer.echo("No harnesses registered.")
        return

    probe_spec = AgentSpec(
        harness="",
        workdir="/",
        role=None,
        skills=[],
        rules=[],
        harness_spec=None,
    )
    typer.echo(f"Runtime {runtime.name or runtime.id} ({runtime.id})")
    typer.echo(f"{'HARNESS':<20} STATUS")
    typer.echo("-" * 36)
    for harness_name in harnesses:
        harness_cls = get_harness_cls(harness_name)
        spec = probe_spec.model_copy(update={"harness": harness_name})
        harness = harness_cls.from_spec(spec)
        try:
            installed = harness.is_installed(backend)
        except Exception:
            installed = False
        status = "installed" if installed else "not installed"
        typer.echo(f"{harness_name:<20} {status}")


@runtime_app.command(
    "exec",
)
def runtime_exec(
    ref: str = typer.Argument(
        ..., help="Runtime name or ID", autocompletion=_complete_runtime_ref
    ),
    command: list[str] = typer.Argument(
        ...,
        metavar="COMMAND [ARGS]...",
        help=_help("Command to run inside runtime. Use `--` before command if needed."),
    ),
    cwd: str = typer.Option("/", "--cwd", "-C", help="Working directory in runtime"),
    env_entries: list[str] | None = typer.Option(
        None, "--env", "-e", help="KEY=VALUE env (repeatable)"
    ),
    timeout: float | None = typer.Option(
        None, "--timeout", "-t", help="Execution timeout in seconds"
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help="Attach stdin/stdout/stderr for interactive execution",
    ),
) -> None:
    """Execute a command inside a runtime (for quick testing/debug)."""
    env = _parse_env(env_entries)
    if interactive:
        if timeout is not None:
            raise typer.BadParameter("--timeout is not supported with --interactive")
        try:
            _runtime_api().exec_fg(
                ref,
                command,
                cwd=cwd,
                env=env,
            )
        except RuntimeError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        return

    result = _runtime_api().exec(
        ref,
        command,
        cwd=cwd,
        env=env,
        timeout=timeout,
    )
    if result.stdout:
        typer.echo(result.stdout, nl=False)
    if result.stderr:
        typer.echo(result.stderr, nl=False, err=True)
    if result.exit_code != 0:
        raise typer.Exit(result.exit_code)


# ---------------------------------------------------------------------------
# Agent commands
# ---------------------------------------------------------------------------


@agent_app.command("create")
def agent_create(
    runtime_ref: str = typer.Option(
        ...,
        "--runtime",
        "-R",
        help="Runtime name or ID",
        autocompletion=_complete_runtime_ref,
    ),
    harness: str = typer.Option(
        ...,
        "--harness",
        "-H",
        callback=_validate_harness,
        autocompletion=_complete_harness,
        help="Harness type (dynamic; see: saddler runtime harnesses <id|name>)",
    ),
    workdir: str = typer.Option(
        ..., "--workdir", "-w", help="Working directory inside the runtime"
    ),
    role: str | None = typer.Option(
        None, "--role", "-r", help="Role source path or URL"
    ),
    skill: list[str] | None = typer.Option(
        None, "--skill", "-s", help=_help("[name@]source (repeatable)")
    ),
    rule: list[str] | None = typer.Option(
        None, "--rule", help=_help("[name@]source (repeatable)")
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Display name"),
    meta: list[str] | None = typer.Option(
        None, "--meta", "-m", help="KEY=VALUE metadata (repeatable)"
    ),
) -> None:
    """Assemble an agent (install harness, rules, skills)."""
    rec = _agent_api().create(
        AgentCreateRequest(
            runtime_ref=runtime_ref,
            harness=harness,
            workdir=workdir,
            role=ResourceCreateSpec(name="role", source=role) if role else None,
            skills=[_parse_resource(s) for s in (skill or [])],
            rules=[_parse_resource(r) for r in (rule or [])],
            name=name,
            metadata=_parse_metadata(meta),
        )
    )
    label = rec.name or rec.id
    typer.echo(f"Created agent {label} ({rec.id})")
    typer.echo(f"  Next: saddler agent tui {label}", err=False)


@agent_app.command("rm")
def agent_rm(
    ref: str = typer.Argument(
        ..., help="Agent name or ID", autocompletion=_complete_agent_ref
    ),
) -> None:
    """Remove an agent."""
    _agent_api().remove(ref)
    typer.echo(f"Removed agent {ref}.")


@agent_app.command("ls")
def agent_ls() -> None:
    """List all agents."""
    records = _agent_api().list()
    if not records:
        typer.echo("No agents found.")
        return
    typer.echo(f"{'ID':<34} {'NAME':<20} {'HARNESS':<14} {'RUNTIME':<34} WORKDIR")
    typer.echo("-" * 120)
    for r in records:
        typer.echo(
            f"{r.id:<34} {(r.name or '-'):<20} {r.spec.harness:<14} "
            f"{r.runtime:<34} {r.spec.workdir}"
        )


@agent_app.command("inspect")
def agent_inspect(
    ref: str = typer.Argument(
        ..., help="Agent name or ID", autocompletion=_complete_agent_ref
    ),
) -> None:
    """Show full details of an agent."""
    rec = _agent_api().inspect(ref)
    typer.echo(
        yaml.safe_dump(
            rec.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        ).strip()
    )


@agent_app.command("tui")
def agent_tui(
    ref: str = typer.Argument(
        ..., help="Agent name or ID", autocompletion=_complete_agent_ref
    ),
) -> None:
    """Launch the harness in interactive terminal mode (for human use)."""
    _agent_api().tui(ref, tty=sys.stdin.isatty())
    typer.echo(f"Harness session ended for agent {ref}.")


@agent_app.command("acp")
def agent_acp(
    ref: str = typer.Argument(
        ..., help="Agent name or ID", autocompletion=_complete_agent_ref
    ),
) -> None:
    """Launch the harness in ACP server mode (for machine/API clients)."""
    _agent_api().acp(ref, tty=sys.stdin.isatty())
    typer.echo(f"ACP session ended for agent {ref}.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        app()
    except AppError as e:
        typer.echo(f"Error: {e}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
