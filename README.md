# mini-agent

> 一个可本地运行、可扩展的最小 AI Agent 框架。同时提供命令行 REPL 与浏览器 WebUI 两种入口；支持多会话、多轮对话、工具调用、权限管控、定时任务、外部 Hook 扩展、子智能体协作等完整能力。

![python](https://img.shields.io/badge/python-%E2%89%A53.10-blue)
![framework](https://img.shields.io/badge/framework-FastAPI%20%2B%20Anthropic%20SDK-green)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

## 亮点

- **两种入口**：命令行 REPL（`python -m agents`） 与 浏览器 WebUI（`python -m agents.webui`），共用同一套 agent 核心
- **完整权限管线**：bash 安全校验 → deny 黑名单 → 模式判定（default/plan/auto）→ allow 规则 → 询问用户，五步不可绕过
- **Token 用量可视化**：命令行 HUD + WebUI 条形进度条，对齐 `auto_compact` 触发阈值
- **定时任务**：LLM 自己排"将来要跑的 prompt"，5 字段 cron、持久化、漏触发检测、跨进程锁
- **外部 Hook 系统**：`.hooks.json` 挂 shell 脚本到 `SessionStart` / `PreToolUse` / `PostToolUse` / `RoundEnd`，跨语言退出码契约
- **子智能体 / 团队协作**：`run_subagent` + `MessageBus` + `TeammateManager`，支持独立 agent 间的消息通信与协作
- **上下文压缩**：轻量 `microcompact`（每轮）+ 重度 `auto_compact`（超阈值自动触发），原对话备份到 `.transcripts/`
- **Skills 加载**：`skills/` 目录下的 `SKILL.md` 文件自动加载为可调用技能
- **多会话隔离（WebUI）**：每个会话独立 history + 独立权限管理器，持久化到 `.claude/webui_sessions/`，刷新不丢
- **极小依赖**：核心只要 `anthropic` + `python-dotenv`；WebUI 额外 `fastapi` + `uvicorn` + `pydantic`，前端纯原生 HTML/CSS/ES module，**无 npm 构建**

---

## 快速开始

### 1. 安装

```bash
cd learn-claude-code-mini
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 MODEL_ID 以及 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN
```

Python ≥ 3.10（使用了 `Path.is_relative_to`、PEP 604 `X | None` 等新语法）。

### 2. 选择入口

#### A. 命令行 REPL

```bash
python -m agents                    # 启动 REPL，会先选择权限模式
python -m agents repl --mode auto   # 直接以 auto 权限进入
```

#### B. 浏览器 WebUI

```bash
python -m agents.webui              # 默认 http://127.0.0.1:8765，自动打开浏览器
python -m agents.webui --port 9000 --no-open
```

WebUI 一页三栏：左栏会话列表 + 定时任务面板，中栏对话流（顶部 ctx HUD、底部输入 + 斜杠补全），权限请求以模态框弹出。

#### C. 单轮非交互

```bash
python -m agents run -p "列出当前目录的所有 python 文件并统计行数"
python -m agents tasks              # 列出所有文件任务
python -m agents team               # 列出 teammate 状态
python -m agents rules --mode plan  # 展示 plan 模式下的默认权限规则
```

---

## 使用指南

### 权限模式

启动时或运行中可切换三种模式：

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| `default` | 遵循默认规则，非白名单工具逐个询问 | 日常使用（推荐） |
| `plan` | 所有写工具一律 deny，其余通过 | 只想让它规划、不动磁盘 |
| `auto` | 只读工具秒过，其它仍需询问 | 熟悉工作流后加速体验 |

切换方式：CLI 里 `/mode auto`；WebUI 顶部下拉。

### 斜杠命令（REPL 与 WebUI 通用）

| 命令 | 用途 |
|------|------|
| `/compact` | 手动触发 auto_compact（重度压缩对话历史） |
| `/clear [hard]` | 清对话历史；`hard` 连 token 用量与 todos 一并清 |
| `/mode <default\|plan\|auto>` | 切换权限模式 |
| `/usage [reset]` | 打印 token 用量；`reset` 清零 |
| `/cron [list\|clear [hard]\|test [prompt]]` | 定时任务查看 / 清空 / 手动触发 |
| `/tasks` | 列出 `.tasks/` 下所有文件任务 |
| `/team` | 列出 teammate 及状态 |
| `/inbox` | 读取并清空 lead 收件箱 |
| `/rules` | 展示当前所有权限规则 |
| `/prompt` | 打印当前 system prompt 全文 |
| `/sections` | 打印 system prompt 各段占用字符数 |
| `/hooks [on\|off\|reload\|list]` | 外部 hook 启停与查看（仅 REPL） |

WebUI 里输入 `/` 会弹出补全浮层：↑/↓ 选择，Enter 确认（带参数的命令会塞到输入框，不带参数的直接执行）。

### 基础工具

LLM 可用的内置工具：

- **文件**：`read_file` / `write_file` / `edit_file`
- **Shell**：`bash` / `bash_readonly`
- **搜索**：`search_content` / `web_search` / `web_fetch`
- **任务**：`task_create` / `task_list` / `task_update`（持久化文件任务）
- **子智能体**：`run_subagent`（用另一个独立上下文跑子任务）
- **Todo**：`TodoWrite`（进度跟踪 + 自动 nag 提醒）
- **Skills**：`load_skill`（加载 `skills/<name>/SKILL.md`）
- **压缩**：`compress`（LLM 主动压缩）
- **定时任务**：`cron_list` / `cron_create` / `cron_delete`
- **团队协作**：`read_inbox` / 消息总线 / teammate 管理

---

## 核心机制

### 1. 权限管线

`PermissionManager.check()` 五步（顺序不可换）：

1. **Bash 安全校验** — `sudo`、`rm -r*` 立即 `deny`；命令替换 `$()` / `;&|` / IFS 注入等升级为 `ask`
2. **Deny 规则** — 旁路免疫的黑名单，auto 模式也绕不过
3. **模式判定** — `plan` / `auto` / `default` 各自的快速通道
4. **Allow 规则** — 白名单直通（典型只读 bash、`read_file` 等）
5. **询问用户** — CLI 走箭头菜单 / y·n·always；WebUI 弹模态框走 WebSocket 回传

**断路器**：连续 3 次 deny 给出提示，建议切 plan 模式。

**teammate 场景**：后台线程无法交互，`ask` 自动退化为 `deny`，原因作为 `tool_result` 回传，LLM 会自己调整策略。

### 2. Hook 扩展

在工作区根放 `.hooks.json`，就能把任意 shell 命令挂到以下事件：

| 事件 | 触发时机 | 典型用途 |
|------|---------|---------|
| `SessionStart` | REPL / WebUI 启动一次 | 打印 git 状态、预热缓存、加载偏好 |
| `PreToolUse` | 每次工具调用前（权限 allow 之后） | 二次安全扫描、参数脱敏、路径规范化 |
| `PostToolUse` | 每次工具调用后 | 审计日志、自动 lint、追加 reminder |
| `RoundEnd` | 每轮 `agent_loop` 自然结束时（LLM 返回纯文本） | 每轮摘要落盘、轮次计数、时间戳记录 |

**退出码契约**（跨语言通用）：

- `0` → continue（什么都不做）
- `1` → block（工具不执行；stderr 作为阻断理由回给 LLM。仅 PreToolUse 有拦截意义）
- `2` → inject（工具正常执行；stderr 追加到 tool_result）

**环境变量**：`HOOK_EVENT` / `HOOK_TOOL_NAME` / `HOOK_TOOL_INPUT`（JSON 字符串） / `HOOK_TOOL_OUTPUT`（仅 PostToolUse）。

> RoundEnd 下 `HOOK_TOOL_NAME` 固定为空；`HOOK_TOOL_INPUT` 是 `{"stop_reason": "...", "last_assistant_text": "..."}`，方便脚本做每轮摘要。

**结构化 stdout**（可选）：stdout 若是合法 JSON，支持两个扩展字段：
- `updatedInput`：仅 PreToolUse，覆盖原 tool_input
- `additionalContext`：等价 `exit 2` + stderr 的注入

**启停**：

- 无 `.hooks.json` → HookManager 空转
- `.hooks.disabled` 空文件 → 磁盘级长期禁用
- REPL 里 `/hooks off` / `/hooks reload` → 运行时控制

示例：拷贝 `.hooks.json.example` 到 `.hooks.json` 即可启用一组 demo hooks（含安全扫描、审计日志、每轮摘要落盘）。

### 3. 定时任务（Cron）

"让 agent 给自己排将来要跑的 prompt"。流程：

```
LLM 调用 cron_create
    ↓
后台线程每分钟匹配 cron 表达式
    ↓ 到点
① 普通模式：prompt 投入通知队列 → 下一轮 agent_loop 注入为 <scheduled-tasks> → LLM 决定怎么处理
② auto_run 模式：直接 spawn 一个子 agent 独立执行，无需等下一轮
```

**Cron 表达式**（5 字段，纯手写解析，零依赖）：

```
min   hour   dom   month   dow           (dow: 0=周日)
支持：* · N · N-M · N,M · */N · N-M/S

例：
    */5 * * * *     每 5 分钟
    0 9 * * 1       周一上午 9:00
    30 14 * * *     每日 14:30
```

**持久化 × 触发类型**：

| | recurring | one-shot |
|---|---|---|
| session-only（默认） | 内存列表，进程退出即丢 | 同上 |
| durable | `.claude/scheduled_tasks.json`，7 天自动过期 | 同上；触发一次后从磁盘删除 |

**鲁棒性**：

- **整点抖动**：分钟字段落在 `:00` / `:30` 时，依 `hash(表达式)` 加 1-4 分钟偏移，避免多任务整点雪崩
- **漏触发检测**：进程启动时回看每个 durable 任务的 `last_fired → now`（最多 24h），若错过至少一次匹配则注入 `[Missed scheduled task ...]`，让 LLM 决定要不要补
- **跨进程锁**：`.claude/cron.lock`（PID 存活探针），多开 REPL 只有一个实例触发；崩溃残留锁自动接管

**cron 本身不提权**：到点的 prompt 最终仍由 LLM 发工具调用，仍走完整的权限管线。

### 4. 上下文压缩

- **microcompact**（每轮必做）：裁剪老旧 tool_result，只保留最近 N 条 + `read_file` 这类事实型工具的原文
- **auto_compact**（token 超阈值自动触发）：把整段对话总结成一条 assistant 消息；完整原对话备份到 `.transcripts/transcript_<ts>.jsonl`
- **手动触发**：`/compact` 或 LLM 调用 `compress` 工具

### 5. Token 用量监控

每次 LLM 响应后把 `usage` 累计到 `UsageTracker`：

- **CLI HUD**：`ctx ███░░░░░░░ 12% · in 4,512 out 1,203 · total 5,715/100,000 · Δin 312 Δout 87`
- **WebUI HUD**：顶部条形进度条，50%/80% 三档绿/黄/红变色，对齐 `auto_compact` 触发阈值

### 6. 子智能体与团队

- **子智能体**：`run_subagent(prompt, agent_type)` 在独立上下文跑一段"用完即弃"的任务，结果摘要返回主 agent
- **文件任务**（`.tasks/task_<id>.json`）：支持多 agent 抢占式协作
- **消息总线** + **teammate**：`MessageBus` 按角色分收件箱；`TeammateManager` 可注册多个协作 agent，互相通过 `send_message` 通信

---

## WebUI 技术概览

### 架构

- **后端**：FastAPI + Uvicorn。SSE（`/api/stream/{sid}` + `/api/stream/global`）推对话事件、cron 触发、通知；WebSocket（`/api/ws/{sid}`）用于权限 ask 同步回传
- **前端**：原生 HTML + CSS + ES module，零构建、零依赖
- **并发**：每个会话一个常驻 worker 线程 + `queue.Queue` 事件总线；多浏览器标签同时订阅同一会话走 fan-out

### 体验细节

- **细粒度进度**：通过 `agent_loop` 的 `progress` 回调，前端能实时看到"LLM 思考中 N.Ns"→"执行工具 xxx"→"✓ 完成 · 1.2s"
- **工具卡片**：每个 tool_use 独立可折叠卡片，运行中橙色边框 + 旋转 spinner，完成变绿边框 + 耗时，失败变红边框
- **斜杠补全**：输入 `/` 弹浮层，↑↓ 选择，Enter 执行，Tab 补全
- **权限模态框**：显示工具名 + 参数 JSON + 倒计时；Enter 允许 / Esc 拒绝，180s 无响应按拒绝处理
- **通知 toast**：cron 触发、auto_run 完成、错误按严重度着色滑入右下

### 对 agents 核心的侵入（最小）

只做两处向后兼容的扩展：
- `PermissionManager(ask_callback=...)`：未注入时走原终端交互
- `CronScheduler.add_event_listener(cb)`：零监听者时完全无影响
- `agent_loop(progress=...)`：可选参数，None 时主循环行为完全不变

**CLI / REPL / teammate 路径零感知**。

---

## 目录结构

```
learn-claude-code-mini/
├── README.md · requirements.txt · .env.example · .hooks.json.example
└── agents/
    ├── __main__.py              # python -m agents 入口
    ├── core/
    │   ├── config.py            # 环境变量 / client / 路径 / 阈值
    │   ├── prompts.py           # System prompt 装配（SystemPromptBuilder）
    │   ├── runtime.py           # 全局单例 + build_perms 工厂
    │   ├── dispatch.py          # TOOL_HANDLERS / TOOLS schema
    │   ├── hooks.py             # HookManager + 退出码契约
    │   ├── usage.py             # Token 用量追踪 + HUD 渲染
    │   └── loop.py              # agent_loop（权限 + hook + progress）
    ├── tools/
    │   ├── persisted_output.py  # 大结果落盘
    │   ├── fs.py                # safe_path / read / write / edit
    │   ├── bash.py              # run_bash
    │   └── subagent.py          # run_subagent
    ├── managers/
    │   ├── todos.py · skills.py · compression.py
    │   ├── file_tasks.py        # 文件任务（多 agent 协作）
    │   ├── background.py        # 后台任务
    │   └── scheduler.py         # CronScheduler + CronLock
    ├── permissions/
    │   ├── validators.py        # BashSecurityValidator
    │   └── manager.py           # PermissionManager + 默认规则
    ├── team/
    │   ├── messaging.py         # MessageBus
    │   ├── protocols.py         # shutdown / plan 协议
    │   └── teammate.py          # TeammateManager
    ├── cli/
    │   ├── main.py              # argparse 子命令
    │   └── repl.py              # REPL 循环
    └── webui/                   # 浏览器 UI（与 cli 并列）
        ├── __main__.py          # python -m agents.webui
        ├── server.py            # FastAPI app
        ├── session.py · session_manager.py
        ├── events.py · usage_tracker.py · cron_bridge.py
        ├── slash_commands.py
        ├── api/                 # REST / SSE / WebSocket
        └── static/              # 单页前端
```

## 运行时落盘目录

| 路径 | 用途 |
|------|------|
| `.claude/webui_sessions/<id>.json` | WebUI 会话历史持久化 |
| `.claude/scheduled_tasks.json` | durable 定时任务 |
| `.claude/cron.lock` | 定时任务跨进程锁（PID 文件） |
| `.transcripts/transcript_<ts>.jsonl` | auto_compact 前的完整对话备份 |
| `.task_outputs/tool-results/<id>.txt` | 超阈值工具输出落盘 |
| `.tasks/task_<id>.json` | 文件任务 |
| `.team/config.json` · `.team/inbox/<name>.jsonl` | 团队配置与收件箱 |
| `skills/<name>/SKILL.md` | 技能定义（frontmatter + body） |
| `.hooks.json` · `.hooks.disabled` | Hook 配置与禁用标记 |

---

## 性能与资源

| 维度 | 说明 |
|------|------|
| **启动开销** | CLI 约 300–500ms（含 Anthropic client 初始化）；WebUI 约 1–2s（含 uvicorn + cron 启动） |
| **内存占用** | 空载约 60–80 MB；每个活跃会话 worker 线程额外 ~5 MB |
| **每轮 LLM 调用延迟** | 完全由上游模型决定，本地开销（权限检查 + hook 调用 + 工具分派）通常 <50ms |
| **压缩阈值** | `TOKEN_THRESHOLD = 100_000`（可在 `core/config.py` 调整）；过阈自动触发重度压缩 |
| **Cron 粒度** | 1 秒唤醒 + 分钟级去重；每任务每分钟最多触发一次 |
| **Hook 超时** | 单条 shell 命令 30s 硬超时（`core/hooks.py: HOOK_TIMEOUT`） |
| **权限询问超时（WebUI）** | 180s 无前端响应自动按 deny 处理 |
| **SSE 心跳** | 15s 一次，防代理断连 |

---

## 可扩展点

想自己加功能时，首选这几个不需要改核心的扩展机制：

1. **Hook 脚本**（零侵入）：用任意语言往 `.hooks.json` 挂 pre/post/roundend 脚本
2. **Skills**：在 `skills/<name>/SKILL.md` 写一段 markdown，frontmatter 声明触发词，正文就是注入给 LLM 的指南
3. **自定义工具**：`agents/core/dispatch.py` 加 handler + schema，`agents/tools/` 放实现
4. **自定义 teammate**：`agents/team/teammate.py` 按协议新建一个独立 agent 角色，通过 MessageBus 与 lead 通信

---

## FAQ

**Q：CLI 和 WebUI 可以同时开吗？**
不行。两者共用全局单例（包括 `CronScheduler`），而 cron 用文件锁（`.claude/cron.lock`）保证同一时刻只有一个进程能真正触发定时任务。同时开会导致后启的那个进入"只读"模式。

**Q：WebUI 能远程访问吗？**
默认只绑 `127.0.0.1`。如果要暴露到局域网，用 `--host 0.0.0.0`，但请注意权限模式至少设为 `plan`，否则相当于把 shell 暴露给网络。

**Q：权限规则能配置吗？**
规则目前硬编码在 `agents/permissions/manager.py: DEFAULT_RULES`。运行时通过 "always" 选项可以向当前会话追加动态规则（进程级，退出即丢）。要持久化请直接改代码。

**Q：支持流式输出（一个 token 一个 token 显示）吗？**
目前是"分段流式"：每个工具调用、每次 LLM 往返都会立即推事件给前端。token 级流式需要把 `client.messages.create` 换成 `client.messages.stream()`，会动到 agent_loop，暂未支持。

**Q：如何接入其他模型？**
`agents/core/config.py` 里的 `client = Anthropic(base_url=...)` 读 `ANTHROPIC_BASE_URL`，任何兼容 Anthropic API 的网关都能接入（包括通过代理层桥接的 OpenAI / Gemini / 国产模型）。

---

## 许可

MIT
