# saddler

English | [简体中文](README.zh-CN.md)

`saddler` is a declarative CLI for building and operating agent environments.

It manages **storage**, **runtime**, and **agent** lifecycles in one place, and provides `hitch` for compose-style multi-agent orchestration.

## Why saddler

- Unified lifecycle management for storages, runtimes, and agents
- Pluggable harnesses (for example `codex`, `cursor`, `gemini`, `openclaw`, `opencode`, `claude_code`)
- Declarative resource assembly from local paths or remote sources
- Repeatable environment setup with `hitch` (`up`, `down`, `ps`, `config`)
- Python-first workflow with `uv` and Typer-based CLI ergonomics

> [!NOTE]
> `saddler` is under active iteration. Backward compatibility between early versions is not guaranteed.

## Requirements

- Python `3.11+`
- `uv` (recommended package manager)
- Optional: Docker (for docker runtime backend)

## Installation

Install from PyPI:

```bash
pip install saddler
```

or with `uv`:

```bash
uv tool install saddler
```

or run directly with `uvx`:

```bash
uvx saddler --help
```

Install from source (for contributors):

```bash
git clone https://github.com/hopeful0/saddler.git
cd saddler
uv sync
```

Validate installation:

```bash
uv run saddler --help
```

## Using saddler with uvx

For ad-hoc usage without global installation:

```bash
uvx saddler --help
```

For local source-tree testing:

```bash
uvx --from . saddler --help
```

For daily usage after installation:

```bash
saddler --help
```

> [!TIP]
> `uvx saddler ...` is ideal for quick runs. `uv tool install saddler` is better for day-to-day usage.

## Quick Start

### 1) Create and start a runtime

```bash
saddler runtime create local --name dev-local
saddler runtime start dev-local
```

### 2) Create an agent on that runtime

```bash
saddler agent create \
  --runtime dev-local \
  --harness codex \
  --workdir /workspace \
  --name my-agent
```

### 3) Launch the harness session

```bash
saddler agent tui my-agent
```

### 4) Inspect current resources

```bash
saddler runtime ls
saddler agent ls
saddler storage ls
```

## Core Commands

- `saddler agent create|ls|inspect|tui|acp|rm`
- `saddler runtime create|start|stop|ls|inspect|harnesses|rm`
- `saddler storage create|ls|inspect|rm`
- `saddler hitch config|up|stop|down|ps`

Common aliases:

- `saddler agent` can also be called from top-level high-frequency commands
- `saddler runtime` has alias `saddler rt`
- `saddler storage` has alias `saddler st`

## Runtime Creation Patterns

Create local runtime:

```bash
saddler runtime create local --name local-dev
```

Create docker runtime:

```bash
saddler runtime create docker \
  --name docker-dev \
  --image saddler-runtime:all \
  --user 1000:1000 \
  --mount .:/workspace
```

Create with dynamic backend type:

```bash
saddler runtime create --type <backend> --opt key=value
```

> [!TIP]
> Use `saddler runtime harnesses <runtime-ref>` to verify which harness dependencies are already installed in that runtime.

## Hitch: Compose-style Orchestration

`hitch` treats agent stacks like a compose project and helps you bring everything up/down reproducibly.

Minimal example:

```yaml
name: demo
version: 1

runtimes:
  rt:
    backend: docker
    backend_spec:
      image: saddler-runtime:all
      user: 1000:1000
    mounts:
      - .:/workspace

agents:
  main:
    harness: opencode
    workdir: /workspace
    runtime: rt
```

Typical workflow:

```bash
saddler hitch config -f hitch.local.yml
saddler hitch up -f hitch.local.yml
saddler hitch ps -f hitch.local.yml
saddler hitch down -f hitch.local.yml
```

## Shell Completion

`saddler` supports native Typer completion for `zsh`, `bash`, and `fish`.

- zsh:
  - `echo 'eval "$(_SADDLER_COMPLETE=zsh_source saddler)"' >> ~/.zshrc`
  - `source ~/.zshrc`
- bash:
  - `echo 'eval "$(_SADDLER_COMPLETE=bash_source saddler)"' >> ~/.bashrc`
  - `source ~/.bashrc`
- fish:
  - `echo '_SADDLER_COMPLETE=fish_source saddler | source' >> ~/.config/fish/config.fish`
  - `source ~/.config/fish/config.fish`

## Troubleshooting

- `runtime harnesses` shows `not installed`
  - Install required harness dependencies in the runtime, then rerun `runtime harnesses`
- Docker runtime cannot start
  - Confirm Docker daemon is running and your user can run `docker`
- Bind mount errors
  - Ensure destination is absolute and host source path exists
- Completion not effective
  - Reload shell config (`source ~/.zshrc`, `source ~/.bashrc`, or fish config)
