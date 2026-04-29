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

## Commits

Follow [Conventional Commits](https://www.conventionalcommits.org/). Format: `type(scope): 描述`

Types: `feat` · `fix` · `refactor` · `perf` · `test` · `docs` · `style` · `chore` · `ci` · `revert`

- Scope: module name (`runtime`, `harness`, `hitch`, `cli`, `storage`, …); omit if cross-cutting
- Description in Chinese, imperative, no period
- Breaking changes: `feat(api)!: …` + `BREAKING CHANGE:` footer
- Before committing: `uv run pytest && uv run ruff check . && uv run ruff format --check .` must pass

## Editing

When making a targeted edit (fix, correction, update to match reality), only change what's required. Do not embed explanations of the change into the file itself — those belong in the conversation or commit message.

## Code Style

- Pydantic models for all persistent data; dataclasses for transient request/response objects in API layer
- No comments unless the *why* is non-obvious — well-named identifiers are preferred
- New storage/runtime/harness types: add to `infra/`, register via `@register_*` decorator, declare entry point in `pyproject.toml`
- `Repository.mutate(id, fn)` — always prefer over fetch-then-update
- Discriminated unions: always add a `Literal` type field when extending mount or spec variants
- Hitch metadata: user keys must not use the `hitch.` prefix

## Testing

- **No mocking**: use fake objects (`SimpleNamespace`, `Fake*Api` classes) or `InMemoryRepository` instead
- **CLI tests**: monkeypatch the lazily-initialized `_*_api()` functions in `saddler.cli`
- **UseCase / executor tests**: pass `InMemoryRepository` instances via `build_use_cases(persist=False)` or direct constructor injection
- **Harness tests**: instantiate the harness class directly with a fake `RuntimeBackend` implementation
- Tests are organized by layer: `tests/cli/`, `tests/hitch/`, `tests/infra/`, `tests/resource/`, `tests/shared/`
- Each test file covers one logical unit; avoid cross-layer assertions in a single test

## Persistence

Default root: `~/.saddler/`. Layout: `storages/`, `runtimes/`, `agents/` — one JSON file per resource ID. Override via `build_use_cases(root=...)`.
