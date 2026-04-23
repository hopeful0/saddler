# saddler

[English](README.md) | 简体中文

`saddler` 是一个用于构建和管理 Agent 运行环境的声明式 CLI。

它统一管理 **storage**、**runtime**、**agent** 生命周期，并提供 `hitch` 进行类似 compose 的多 Agent 编排。

## 为什么选择 saddler

- 统一管理 storages、runtimes、agents 生命周期
- 可插拔 harness（例如 `codex`、`cursor`、`gemini`、`openclaw`、`opencode`、`claude_code`）
- 支持从本地路径或远程源声明式组装资源
- 使用 `hitch` 实现可复现环境编排（`up`、`down`、`ps`、`config`）
- 基于 Python + `uv` + Typer 的工程化 CLI 工作流

> [!NOTE]
> `saddler` 仍处于快速迭代阶段，早期版本之间不保证向后兼容。

## 环境要求

- Python `3.11+`
- `uv`（推荐依赖管理工具）
- 可选：Docker（用于 docker runtime backend）

## 安装

```bash
git clone <your-repo-url>
cd saddler
uv sync
```

验证安装：

```bash
uv run saddler --help
```

## 使用 uvx

如果你偏好 `uvx` 工作流，可以不手动激活环境直接运行 `saddler`。

从当前源码目录直接运行：

```bash
uvx --from . saddler --help
```

将当前项目安装为全局 uv 工具（一次性），后续直接使用 `saddler`：

```bash
uv tool install .
saddler --help
```

拉取新代码后升级工具：

```bash
uv tool upgrade saddler
```

> [!TIP]
> `uvx --from . saddler ...` 更适合快速本地试跑；`uv tool install .` 更适合日常长期使用。

## 快速开始

### 1) 创建并启动 runtime

```bash
uv run saddler runtime create local --name dev-local
uv run saddler runtime start dev-local
```

### 2) 在该 runtime 上创建 agent

```bash
uv run saddler agent create \
  --runtime dev-local \
  --harness codex \
  --workdir /workspace \
  --name my-agent
```

### 3) 启动 harness 会话

```bash
uv run saddler agent tui my-agent
```

### 4) 查看当前资源

```bash
uv run saddler runtime ls
uv run saddler agent ls
uv run saddler storage ls
```

## 核心命令

- `saddler agent create|ls|inspect|tui|acp|rm`
- `saddler runtime create|start|stop|ls|inspect|harnesses|rm`
- `saddler storage create|ls|inspect|rm`
- `saddler hitch config|up|stop|down|ps`

常用别名：

- `saddler agent` 支持顶层高频命令调用
- `saddler runtime` 的别名是 `saddler rt`
- `saddler storage` 的别名是 `saddler st`

## Runtime 创建方式

创建本地 runtime：

```bash
uv run saddler runtime create local --name local-dev
```

创建 docker runtime：

```bash
uv run saddler runtime create docker \
  --name docker-dev \
  --image saddler-runtime:all \
  --user 1000:1000 \
  --mount .:/workspace
```

通过动态 backend 类型创建：

```bash
uv run saddler runtime create --type <backend> --opt key=value
```

> [!TIP]
> 使用 `uv run saddler runtime harnesses <runtime-ref>` 可检查该 runtime 已安装哪些 harness 依赖。

## Hitch：类 Compose 编排

`hitch` 将 agent 栈按 compose 项目方式管理，帮助你可重复地 `up/down`。

最小示例：

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

典型工作流：

```bash
uv run saddler hitch config -f hitch.local.yml
uv run saddler hitch up -f hitch.local.yml
uv run saddler hitch ps -f hitch.local.yml
uv run saddler hitch down -f hitch.local.yml
```

## 命令补全

`saddler` 支持 Typer 原生命令补全（`zsh`、`bash`、`fish`）。

- zsh:
  - `echo 'eval "$(_SADDLER_COMPLETE=zsh_source saddler)"' >> ~/.zshrc`
  - `source ~/.zshrc`
- bash:
  - `echo 'eval "$(_SADDLER_COMPLETE=bash_source saddler)"' >> ~/.bashrc`
  - `source ~/.bashrc`
- fish:
  - `echo '_SADDLER_COMPLETE=fish_source saddler | source' >> ~/.config/fish/config.fish`
  - `source ~/.config/fish/config.fish`

## 故障排查

- `runtime harnesses` 显示 `not installed`
  - 安装对应 harness 依赖后重新执行该命令
- Docker runtime 无法启动
  - 检查 Docker daemon 是否运行，以及当前用户是否有 `docker` 执行权限
- bind mount 报错
  - 确保目标路径是绝对路径，并且宿主机源路径存在
- 补全未生效
  - 重新加载 shell 配置（`source ~/.zshrc`、`source ~/.bashrc` 或 fish 配置）
