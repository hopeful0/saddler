# saddler · 产品与技术规范

---

## 壹 · 产品规范

### 1. 概述

saddler 是一个声明式 CLI，将 **agent harness**（如 Claude Code、OpenCode）与角色定义（role）、规则（rule）、技能（skill）装配到运行时环境（runtime）中，产生可运行的 agent 实例，并统一管理 storage / runtime / agent 三类资源的生命周期。

> *名称来源：马具工匠（saddler）负责为马匹套上挽具（harness），使其可被驾驭。*

---

### 2. 核心概念

| 概念 | 职责 |
|------|------|
| **storage** | 可挂载的存储资源（本机目录 / NFS），独立于 runtime 生命周期 |
| **runtime** | agent 的执行环境（local / docker），承载挂载与环境变量；可被多个 agent 共用 |
| **agent** | 核心实例：绑定 runtime，含 harness + workdir + role/skill/rule 装配信息 |
| **harness** | 完整的 agentic loop 实现（Claude Code / OpenCode / Gemini CLI / Codex / Cursor / OpenClaw） |
| **role** | 写入 workdir 的角色文档（Markdown），是名称固定为 `role` 的特殊 rule |
| **rule** | 写入 harness 规则目录的 Markdown 文档，按 harness 约定落盘 |
| **skill** | 写入 harness 技能目录的能力模块（含 SKILL.md），按 harness 约定落盘 |
| **hitch** | 类 Compose 的多资源声明式编排，YAML → Plan → Executor |

#### 2.1 resource（skill / rule）

skill 与 rule 统一使用 `[name@]source` 格式指定来源，source 可为本地路径或远程 git URL / archive。`--role <source>` 是 `--rule role@<source>` 的语法糖。

#### 2.2 harness 安装约定

| harness | rules 目录 | skills 目录 |
|---------|-----------|------------|
| claude-code | `.claude/rules/<name>.md`（并 @import 到 CLAUDE.md） | `.claude/skills/<name>/` |
| opencode | `AGENTS.md`（upsert） | `.opencode/skills/<name>/` |
| gemini | `GEMINI.md`（upsert） | `.gemini/skills/<name>/` |
| codex | `AGENTS.md`（upsert） | `.codex/skills/<name>/` |
| cursor | `.cursor/rules/<name>.mdc`（frontmatter） | `.cursor/skills/<name>/` |
| openclaw | `AGENTS.md`（upsert） | `skills/<name>/` |

#### 2.3 metadata

所有三类资源均支持可选的 `dict[str, str]` metadata，通过 `--meta KEY=VALUE` 在创建时写入。hitch 自动注入系统键 `hitch.project`、`hitch.compose_file`、`hitch.service` 用于归属追踪；用户键不得使用 `hitch.` 前缀。

---

### 3. 命令行接口

#### 3.1 设计原则

- **命令行参数是一等公民**，无 saddler 级配置文件
- agent 命令在根级与 `saddler agent` 下均可用；`runtime` / `rt`、`storage` / `st` 互为别名
- 资源均通过 name 或 ID 引用，解析顺序：精确 ID → 精确 name → 唯一前缀
- **装配与启动分离**：`create` 负责装配 workdir，`tui` / `acp` 负责启动 harness

#### 3.2 命令体系

**Storage**

```
saddler storage create --type <local|nfs> [--path <path>] [--server <host>] [--name ...] [--meta ...]
saddler storage ls / inspect <ref> / rm <ref> [--force]
```

**Runtime**

```
saddler runtime create [--type <backend>] [--env KEY=VAL ...] [--storage <ref>:<dest>[:<mode>] ...]
                        [--name ...] [--meta ...] [--opt KEY=VAL ...]
saddler runtime create local  [OPTIONS]
saddler runtime create docker [OPTIONS] [--image <img>] [--user <user>] [--mount <host>:<dest>[:<mode>] ...]
saddler runtime start / stop / rm [--force] / ls / inspect / harnesses <ref>
```

**Agent**（根级与 `saddler agent` 等价）

```
saddler create  --runtime <ref> --harness <name> --workdir <path>
                [--role <source>] [--skill [name@]source ...] [--rule [name@]source ...]
                [--name ...] [--meta ...]
saddler tui     <ref>      # 前台交互式启动 harness
saddler acp     <ref>      # 前台以 ACP 模式启动 harness
saddler ls / inspect <ref> / rm <ref>
```

**Hitch**

```
saddler hitch config [-f FILE ...]
saddler hitch up     [-f FILE ...] [--force-recreate]
saddler hitch stop / down / ps  [-f FILE ...] [-p PROJECT]
```

**Gateway（扩展）**

```
# 管理面（agent lifecycle）
POST   /agents
GET    /agents
GET    /agents/{agent_id}
DELETE /agents/{agent_id}

# 通信面（ACP）
WS     /agents/{agent_id}/ws
POST   /agents/{agent_id}/sessions
POST   /sessions/{session_id}/input
GET    /sessions/{session_id}/stream
DELETE /sessions/{session_id}
GET    /sessions/active
```

Gateway 当前范围说明：
- 通信面为 ACP（WebSocket + Streamable HTTP）
- 暂不提供 TUI 接口
- 暂不提供独立 session 管理能力扩展（维持现有 active 计数与 I/O 路由）
- 暂不提供创建幂等保证

---

## 贰 · 技术规范

### 4. 技术选型

| 项目 | 选择 |
|------|------|
| 实现语言 | Python |
| CLI 框架 | Typer |
| 数据模型 | Pydantic v2 |
| 持久化 | JSON 文件 + filelock（`~/.saddler/`） |
| 扩展机制 | Python entry points |

---

### 5. 分层架构

```
CLI → API → App → Domain ← Infra
```

- **CLI**：参数解析、输出格式、shell completion
- **API**：薄 facade，将 CLI 请求转换为 UseCase 调用
- **App**：UseCase，编排 domain service + repository，处理跨资源引用（`used_by` / `mounted_by`）
- **Domain**：Pydantic 模型 + 纯业务逻辑，不含 I/O
- **Infra**：RuntimeBackend / Harness / Fetcher 的具体实现，通过 Registry（entry points）注入

各层单向依赖，domain 层只依赖 Protocol，infra 在运行时注册。这使得官方实现与第三方扩展处于同等地位。

---

### 6. 核心设计决策

**装配与启动分离**：`create` 完成 workdir 装配（role、rule、skill 写入），`tui` / `acp` 仅启动 harness 进程。agent 无生命周期状态字段——saddler 不追踪 harness 进程。

**runtime 复用**：runtime 独立于 agent 存在，多个 agent 可共用同一 runtime（`used_by` 追踪引用）。storage 同理（`mounted_by` 追踪）。

**Protocol + Registry**：RuntimeBackend、Harness、Fetcher 均为 `typing.Protocol`，通过 entry point group 注册。运行时执行接口统一为 `RuntimeBackend.exec(...) -> ProcessHandle | None`，并由 `exec_capture` / `exec_fg` / `exec_bg` utility 组合出捕获、前台与后台行为；添加新 harness 或 runtime backend 只需实现协议并注册，无需修改核心代码。

**持久化**：每条资源对应 `~/.saddler/{storages,runtimes,agents}/<id>.json`，`Repository.mutate()` 在 filelock 内原子更新，避免并发覆盖。

**Hitch**：YAML 解析为 `HitchConfig`，经 DAG 校验与拓扑排序生成 `HitchPlan`，`HitchExecutor` 进程内依次调用 API 层——与 CLI 路径共用同一业务层。资源通过 metadata 系统键归属到 project，`down` / `ps` 依此匹配。

---

### 7. 扩展点

| 扩展点 | entry point group | 内置实现 |
|--------|------------------|---------|
| RuntimeBackend | `saddler.runtime.backend` | `local`、`docker` |
| Harness | `saddler.agent.harness` | `claude-code`、`opencode`、`gemini`、`codex`、`cursor`、`openclaw` |
| Fetcher | `saddler.resource.fetcher` | `local`、`git` |
| StorageSpec | `saddler.storage_spec` | `local`、`nfs` |
