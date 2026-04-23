# AGENTS.md

This file provides guidance to agent when working with code in this repository.

## Project Overview

`saddler` is a declarative CLI for building and operating agent environments. It manages **storage**, **runtime**, and **agent** lifecycles in one unified system, with `hitch` for compose-style multi-agent orchestration (think "docker-compose for AI agents"). v0.1.0 — no backward compatibility guarantees.

See [docs/spec.md](docs/spec.md) for product and technical specification.

## Commands

```bash
uv sync                               # Install dependencies
uv run saddler --help                 # Run CLI locally
uv tool install .                     # Install as global tool
uv run pytest                         # Run all tests
uv run pytest tests/path/to/test.py   # Run a single test file
uv run ruff check .                   # Lint
uv run ruff format .                  # Format
uv run prek install                   # Install pre-commit hooks
```

## Architecture

```
CLI (cli.py) → API (api/) → App (app/) → Domain ← Infra (infra/)
                                           ↑
                                      Shared (shared/)
```

- **`saddler/cli.py`** — Typer entry point. All commands live here; use cases are lazily initialized via `@lru_cache`. Shell completion includes dynamic ref resolution.
- **`saddler/api/`** — Thin facades (`RuntimeApiService`, `AgentApiService`, `StorageApiService`) that bridge CLI requests to use cases.
- **`saddler/app/`** — Use case classes. `build_use_cases()` wires everything; `resolver.py` handles name-vs-ID ambiguity.
- **`saddler/agent/`, `saddler/runtime/`, `saddler/storage/`** — Domain models (Pydantic) and services (pure business logic, no I/O).
- **`saddler/infra/`** — Pluggable adapters registered via `Registry[T]`: `runtime/` (LocalBackend, DockerBackend), `harness/` (one file per tool), `fetcher/` (LocalFetcher, GitFetcher), `store/` (JsonFileRepository, InMemoryRepository).
- **`saddler/shared/`** — `Registry[T]` with entry point discovery, `Repository` protocol, `PosixAbsolutePath` type.
- **`saddler/hitch/`** — Compose-style orchestration: `loader.py` → `model.py` → `plan.py` → `executor.py` via API services.
- **`saddler/resource/`** — `ResourceSpec`/`SourceSpec` models; `Fetcher` protocol.

## Key Design Patterns

- **Protocol-based interfaces**: `RuntimeBackend`, `Harness`, `Fetcher`, `Repository` are all `typing.Protocol`. Implement the protocol and register via entry point — no base class needed.
- **Registry + entry points**: Adapters are discovered lazily on first use. `@register_harness_adapter("name")` / `@register_runtime_backend("name")` decorators handle registration.
- **`Repository.mutate(id, fn)`**: Atomic in-place update via callback under filelock — always prefer over fetch-then-update.
- **Discriminated unions**: Mount types use Pydantic discriminators (`type` field). Always add a `Literal` type field when extending mount or spec variants.
- **Hitch metadata**: `hitch.project`, `hitch.compose_file`, `hitch.service` are injected by `hitch up` for lifecycle tracking. User keys must not use `hitch.` prefix.

## Code Style

- Pydantic models for all persistent data; dataclasses for transient request/response objects in API layer
- No comments unless the *why* is non-obvious — well-named identifiers are preferred
- New storage/runtime/harness types: add to `infra/`, register via `@register_*` decorator, declare entry point in `pyproject.toml`

## Testing

- **No mocking**: use fake objects (`SimpleNamespace`, `Fake*Api` classes) or `InMemoryRepository` instead
- **CLI tests**: monkeypatch the lazily-initialized `_*_api()` functions in `saddler.cli`
- **UseCase / executor tests**: pass `InMemoryRepository` instances via `build_use_cases(persist=False)` or direct constructor injection
- **Harness tests**: instantiate the harness class directly with a fake `RuntimeBackend` implementation
- Tests are organized by layer: `tests/cli/`, `tests/hitch/`, `tests/infra/`, `tests/resource/`, `tests/shared/`
- Each test file covers one logical unit; avoid cross-layer assertions in a single test

## Persistence

Default root: `~/.saddler/`. Layout: `storages/`, `runtimes/`, `agents/` — one JSON file per resource ID. Override via `build_use_cases(root=...)`.
