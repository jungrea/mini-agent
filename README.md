# mini-agent

> 一个可本地运行、可扩展的最小 AI Agent 框架。同时提供命令行 REPL 与浏览器 WebUI 两种入口；支持多会话、多轮对话、工具调用、权限管控、定时任务、外部 Hook 扩展、子智能体协作等完整能力。架构源自 learn-claude-code 教程：<https://learn.shareai.run/zh/>

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
- **并行工具执行**：`read_file` / `search_content` / `web_*` 等只读安全工具自动进入并行桶，LLM 同一轮并发请求多个时显著提速；其余工具仍串行以保证顺序与权限交互
- **WebUI 主题切换**：右上角月亮 / 太阳按钮一键切换暗/亮两套 CSS 变量主题，选择持久化到 localStorage
- **消息结构安检**：每次 LLM 调用前走 `normalize_messages`，重排 user 块、补缺失 `tool_result`、剥内部字段、兜底空 content，避免因结构瑕疵被 Anthropic API 拒绝
- **极小依赖**：核心只要 `anthropic` + `python-dotenv`；WebUI 额外 `fastapi` + `uvicorn` + `pydantic`，前端纯原生 HTML/CSS/ES module，**无 npm 构建**

---

## 快速开始

### 1. 安装

```bash
cd mini-agent
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 MODEL_ID 以及 ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY；
# 自己做小项目玩建议申请个 deepseek API，便宜好用，直接改模板填 api key 即可。
# 最新默认模型使用 deepseek-v4-flash，最便宜好用
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

WebUI 一页两栏：左栏会话列表（按工作区自动分组、可折叠），中栏对话流（顶部 ctx HUD、底部输入 + 斜杠补全），权限请求以模态框弹出。

> 一次性的辅助查看能力（文件任务、teammate、收件箱、权限规则）统一通过
> REPL 里的斜杠命令 `/tasks` · `/team` · `/inbox` · `/rules` 提供。

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
- **Shell**：`bash` / `background_run` / `check_background`
- **搜索与网络**：`search_content` / `web_search` / `web_fetch`
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

### 2. 工具并行调度

同一轮 LLM 可能返回多个 `tool_use`（例如并发查 3 个 URL）。`agent_loop` 把工具执行拆成四阶段：

1. **准入（串行）** — 按 `response.content` 原序依次跑权限检查 + `ask_user` + `PreToolUse` hook，收集准入结果
2. **分桶** — 准入通过、工具在 `PARALLEL_SAFE` 白名单里、hook **没改过 input** 的任务进并行桶；其余进串行桶
3. **执行** — 并行桶走 `ThreadPoolExecutor(max_workers=PARALLEL_MAX_WORKERS=3)`；串行桶逐个跑，带 spinner 反馈
4. **收口（串行）** — 按 `response.content` 原序发 `tool_end` 事件 / 跑 `PostToolUse` hook / 拼 `tool_result`，日志与 UI 时序不错位

白名单（`agents/core/dispatch.py: PARALLEL_SAFE`）：`read_file` / `search_content` / `web_fetch` / `web_search`。加入白名单的工具在 `PermissionManager.check()` 里会短路返回 `allow`（默认免审批），扩名单前请确认工具具备 **只读 + 幂等** 两个属性。

并发上限 `PARALLEL_MAX_WORKERS = 3` 可在 `core/loop.py` 调整；超出的任务在线程池内排队。PreHook 改过 `input` 的任务**强制回落到串行**，因为 hook 可能依赖执行顺序。

### 3. 消息结构安检（normalize_messages）

调 Anthropic API 前，`agents/core/normalize.py::normalize_messages` 会过一道安检，专修 4 类常见结构错误：

- **顺序错**：user 消息里的 `tool_result` 块排到最前，`text` 推到后面（Anthropic 要求 tool_use 之后紧跟 tool_result）
- **缺失**：`tool_use_id` 没有配对 `tool_result` → 追加 `(no result - cancelled or lost)` placeholder（避免 400 "tool_use ids were found without tool_result blocks"）
- **内部字段**：剥掉以 `_` 开头的字段（API 不认识的本地元数据）
- **空 content**：`""` / `[]` / `None` 兜底为 `"(empty)"`

`loop` / `subagent` / `teammate` 三处 LLM 调用前统一走这道安检。任何修复动作都会在 stderr 打 `[normalize] xxx` ——看到它说明**源头装配逻辑有瑕疵**，应当去修根源，不要把它当成 bug 修复手段。

### 4. Hook 扩展

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

### 5. 定时任务（Cron）

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

### 6. 上下文压缩

- **microcompact**（每轮必做）：裁剪老旧 tool_result，只保留最近 N 条 + `read_file` 这类事实型工具的原文
- **auto_compact**（token 超阈值自动触发）：把整段对话总结成一条 assistant 消息；完整原对话备份到 `.transcripts/transcript_<ts>.jsonl`
- **手动触发**：`/compact` 或 LLM 调用 `compress` 工具

### 7. Token 用量监控

每次 LLM 响应后把 `usage` 累计到 `UsageTracker`：

- **CLI HUD**：`ctx ███░░░░░░░ 12% · in 4,512 out 1,203 · total 5,715/100,000 · Δin 312 Δout 87`
- **WebUI HUD**：顶部条形进度条，50%/80% 三档绿/黄/红变色，对齐 `auto_compact` 触发阈值

### 8. 子智能体与团队

- **子智能体**：`run_subagent(prompt, agent_type)` 在独立上下文跑一段"用完即弃"的任务，结果摘要返回主 agent
- **文件任务**（`.tasks/task_<id>.json`）：支持多 agent 抢占式协作
- **消息总线** + **teammate**：`MessageBus` 按角色分收件箱；`TeammateManager` 可注册多个协作 agent，互相通过 `send_message` 通信

---

## WebUI 技术概览

### 架构

- **后端**：FastAPI + Uvicorn。SSE（`/api/stream/{sid}` + `/api/stream/global`）推对话事件、cron 触发、通知；权限 ask 回推走**双通道**：REST `/api/sessions/{sid}/permission/resolve`（主通道，可靠）+ WebSocket `/api/ws/{sid}`（低延迟冗余），后端 `resolve_permission_ask` 幂等，两路都触达也不会重复决策
- **前端**：原生 HTML + CSS + ES module，零构建、零依赖
- **并发**：每个会话一个常驻 worker 线程 + `queue.Queue` 事件总线；多浏览器标签同时订阅同一会话走 fan-out

### 体验细节

- **细粒度进度**：通过 `agent_loop` 的 `progress` 回调，前端能实时看到"LLM 思考中 N.Ns"→"执行工具 xxx"→"✓ 完成 · 1.2s"
- **工具卡片**：每个 tool_use 独立卡片，**默认折叠为单行摘要**（工具名 + 关键参数一瞥 + 状态），避免多轮调用刷屏。运行中 / 出错自动展开；点击头部手动切换后，系统会尊重用户的选择、不再自动覆盖。运行中橙色边框 + 旋转 spinner，完成变绿边框 + 耗时，失败变红边框
- **会话按工作区分组**：左栏会话列表自动以 workdir 聚合成可折叠分组（黄色文件夹图标 + 会话计数徽章），折叠状态写 localStorage；包含当前会话的分组始终保持展开
- **斜杠补全**：输入 `/` 弹浮层，↑↓ 选择，Enter 执行，Tab 补全
- **权限模态框**：显示工具名 + 参数 JSON + 倒计时；Enter 允许 / Esc 拒绝，180s 无响应按拒绝处理
- **通知 toast**：cron 触发、auto_run 完成、错误按严重度着色滑入右下
- **暗/亮主题**：右上角 🌙 / ☀ 按钮一键切换，`:root[data-theme="light"]` 覆盖一套 CSS 变量完成整页换肤；`<head>` 内联 script 读 localStorage 防首帧闪屏，选择持久化

### 核心扩展点

WebUI 通过以下三处可选回调 / 监听接入 agent 核心，均为可选参数，不注入时核心行为不受影响：

- `PermissionManager(ask_callback=...)`：前端权限弹窗走此回调回传用户决策
- `CronScheduler.add_event_listener(cb)`：cron 触发 / auto_run 状态桥接到 SSE
- `agent_loop(progress=...)`：把 LLM / 工具的阶段性进度推给前端

---

## 目录结构

```
mini-agent/
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
| `~/.claude/CLAUDE.md` · `<WORKDIR>/.claude/CLAUDE.md` · `<WORKDIR>/CLAUDE.md` | 项目级 AI 指令，被 `SystemPromptBuilder` 按查找链合并进 system prompt（5 层：`~/.claude/` → `<WORKDIR>/.claude/` → `<WORKDIR>/` → `<cwd>/.claude/` → `<cwd>/`，同一路径自动去重） |

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
   - 只读 + 幂等的工具可加入 `PARALLEL_SAFE` 集合，自动进入并行桶 + 免权限审批
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
当前是"分段流式"：每个工具调用、每次 LLM 往返都会立即推事件给前端。token 级流式需要把 `client.messages.create` 换成 `client.messages.stream()` 并重写 `agent_loop` 的收敛逻辑。

**Q：如何接入其他模型？**
`agents/core/config.py` 里的 `client = Anthropic(base_url=...)` 读 `ANTHROPIC_BASE_URL`，任何兼容 Anthropic API 的网关都能接入（包括通过代理层桥接的 OpenAI / Gemini / 国产模型）。模型名通过 `MODEL_ID` 环境变量注入，代码层只此一个入口——换模型只改 `.env` 即可。`.env.example` 里预置了 DeepSeek / MiniMax / GLM / Kimi 的 base_url 与 model id 模板。

---

## 许可

MIT
