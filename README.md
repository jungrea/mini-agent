# mini-agent

> 一个可本地运行、可扩展的最小 AI Agent 框架。同时提供命令行 REPL 与浏览器 WebUI 两种入口；支持多会话、多轮对话、工具调用、权限管控、定时任务、外部 Hook 扩展、子智能体协作等完整能力。架构源自 learn-claude-code 教程：<https://learn.shareai.run/zh/>

![python](https://img.shields.io/badge/python-%E2%89%A53.10-blue)
![framework](https://img.shields.io/badge/framework-FastAPI%20%2B%20Anthropic%20SDK-green)
![license](https://img.shields.io/badge/license-MIT-lightgrey)

---

![WebUI 截图](https://raw.githubusercontent.com/jungrea/mini-agent/main/webui.gif)

---

## 亮点

- **两种入口**：命令行 REPL + 浏览器 WebUI，共用同一套 agent 核心
- **完整权限管线**：五步校验，不可绕过；default/plan/auto 三种模式
- **工具并行执行**：只读安全工具自动并行，其余串行保证顺序
- **定时任务**：LLM 自己排 cron，5 字段表达式、持久化、漏触发检测
- **外部 Hook 系统**：`.hooks.json` 挂 shell 脚本，跨语言退出码契约
- **子智能体 / 团队协作**：独立上下文子任务 + MessageBus 消息通信
- **上下文压缩**：轻量每轮裁剪 + 超 token 阈值自动总结
- **记忆系统**：自动抽取偏好/事实，用户级+项目级双层路由
- **极小依赖**：核心 `anthropic` + `python-dotenv`；前端零构建

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

> **⚠️ 注意**：以下命令使用 `python`，如果你的环境需要 `python3` 才能启动 Python 3，请自行替换（如 `python3 -m agents`）。

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
| `/model <deepseek-v4-flash\|deepseek-v4-pro>` | 切换当前 REPL / WebUI 会话使用的模型；无参数时查看当前模型 |
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

- **权限管线**：Bash 安全校验 → deny 黑名单 → 模式判定（default/plan/auto）→ allow 白名单 → 询问用户，五步顺序不可绕过；连续 3 次 deny 触发断路器建议切 plan 模式
- **工具并行调度**：同一轮多 tool_use 分桶——只读幂等工具（`read_file`/`search_content`/`web_fetch`/`web_search`）并行执行，其余串行保证顺序；准入与收口保持原序
- **消息结构安检**：每次 LLM 调用前 `normalize_messages` 自动修 4 类结构错误（顺序、缺失 tool_result、内部字段、空 content），避免 API 400
- **Hook 扩展**：`.hooks.json` 可挂 shell 脚本到 `SessionStart`/`PreToolUse`/`PostToolUse`/`RoundEnd` 四个事件；退出码契约：0=continue、1=block、2=inject
- **定时任务**：5 字段 cron 表达式（纯手写解析），支持 session-only 与 durable 持久化；整点抖动、漏触发检测、跨进程锁
- **上下文压缩**：每轮 `microcompact` 裁剪老旧 tool_result + 超 token 阈值自动 `auto_compact` 整段总结（原对话备份到 `.transcripts/`）；也可 `/compact` 手动触发
- **记忆系统**：每轮自动从对话抽取偏好/事实，`user` 型→`~/.claude/memory/`（跨项目），其余→`<WORKDIR>/.memory/`（项目级）；下一轮 side-query 选 ≤5 条注入对话
- **Token 用量监控**：CLI HUD + WebUI 进度条，对齐 `auto_compact` 触发阈值
- **子智能体与团队**：`run_subagent` 独立上下文跑子任务；`MessageBus` + `TeammateManager` 支持多 agent 消息通信与协作

---

## WebUI

- **后端**：FastAPI + Uvicorn，SSE 推送对话事件/通知，权限走 REST + WebSocket 双通道
- **前端**：原生 HTML/CSS/ES module，零构建零依赖；暗/亮主题一键切换
- **体验**：工具卡片默认折叠、会话按工作区分组、斜杠命令补全、内置 Markdown 编辑器（实时预览+自动保存）

---

## 目录结构

```
mini-agent/
├── README.md · requirements.txt · .env.example · .hooks.json.example
└── agents/
    ├── __main__.py              # python -m agents 入口
    ├── core/                    # 配置 · prompt装配 · 权限 · hook · 调度 · 主循环
    ├── tools/                   # 文件 · bash · 子agent · 落盘
    ├── managers/                # todo · skill · 压缩 · 记忆 · 任务 · 后台 · 定时
    ├── permissions/             # Bash安全校验 · 权限管理器+规则
    ├── team/                    # 消息总线 · 协议 · teammate管理
    ├── cli/                     # 命令行入口 + REPL
    └── webui/                   # FastAPI服务 + 原生前端
```

## 运行时落盘

| 路径 | 用途 |
|------|------|
| `.claude/webui_sessions/` | WebUI 会话持久化 |
| `.claude/scheduled_tasks.json` | durable 定时任务 |
| `.claude/cron.lock` | 定时任务跨进程锁 |
| `.transcripts/` | auto_compact 前的对话备份 |
| `.task_outputs/` | 超阈值工具输出落盘 |
| `.tasks/` | 文件任务 |
| `.team/` | 团队配置与收件箱 |
| `.memory/` · `~/.claude/memory/` | 项目级 / 用户级记忆 |
| `skills/<name>/SKILL.md` | 技能定义 |
| `.hooks.json` · `.hooks.disabled` | Hook 配置与禁用 |
| `CLAUDE.md`（多层级） | 项目级 AI 指令，合并进 system prompt |

---

## 性能参考

- 启动：CLI ~300-500ms，WebUI ~1-2s
- 内存：空载 ~60-80 MB，每活跃会话 +~5 MB
- 压缩阈值：100,000 tokens（`core/config.py` 可调）
- Hook 超时：30s / 权限询问超时：180s（WebUI）

---

## 可扩展点

1. **Hook 脚本**（零侵入）：`.hooks.json` 挂 pre/post/roundend 脚本
2. **Skills**：`skills/<name>/SKILL.md` 写 markdown 即可注入给 LLM
3. **自定义工具**：`agents/core/dispatch.py` 加 handler + schema；只读幂等工具可入 `PARALLEL_SAFE` 并行免审批
4. **自定义 teammate**：按协议建独立 agent 角色，通过 MessageBus 通信

---

## FAQ

**Q：CLI 和 WebUI 可以同时开吗？**
不行，共用全局单例与 cron 文件锁，后启的进入"只读"模式。

**Q：WebUI 能远程访问吗？**
默认只绑 `127.0.0.1`。用 `--host 0.0.0.0` 可暴露，但权限模式至少设 `plan`。

**Q：权限规则能配置吗？**
硬编码在 `agents/permissions/manager.py: DEFAULT_RULES`；运行时 "always" 选项可追加动态规则（进程级）。

**Q：支持流式输出吗？**
支持。`.env` 设置 `LLM_STREAM=true` 开启 token 级流式；`LLM_THINKING=enabled` + `LLM_THINKING_BUDGET=4096` 可显示思考过程。

**Q：如何切换/接入模型？**
`.env` 的 `MODEL_ID` 为启动默认值；运行时 CLI 用 `/model`，WebUI 用右上角下拉。接入其他模型只需 `ANTHROPIC_BASE_URL` 指向兼容网关，并在 `AVAILABLE_MODELS` 加模型 ID。

**Q：如何关闭记忆系统？**
`.env` 设 `MEMORY_ENABLED=0`；重置则删除 `~/.claude/memory/` 或 `<WORKDIR>/.memory/`。

---

## 教程系列

项目附带 22 篇循序渐进的中文教程，位于 [`doc/`](doc/) 目录，逐模块拆解 Agent 的设计思路与代码实现：

| 阶段 | 章节 | 主题 |
|------|------|------|
| 入门 | s00 | [从一个普通的对话开始](doc/doc_learn/s00_从一个普通的对话开始.md) |
| | s01 | [agent_loop 主循环](doc/doc_learn/s01_agent_loop.md) · [多轮自处理（ReAct）](doc/doc_learn/s01_一个简单的多轮对话.md) |
| 核心能力 | s02 | [多工具系统](doc/doc_learn/s02_多工具系统.md) |
| | s03 | [列任务模式（TodoWrite）](doc/doc_learn/s03_列任务模式.md) |
| | s04 | [子 Agent](doc/doc_learn/s04_子agent.md) |
| | s05 | [Skill 能力模块](doc/doc_learn/s05_关于skill.md) |
| 工程保障 | s06 | [上下文压缩](doc/doc_learn/s06_上下文压缩技术.md) |
| | s07 | [权限管理系统](doc/doc_learn/s07_权限管理系统.md) |
| | s08 | [Hook 钩子系统](doc/doc_learn/s08_hook钩子系统.md) |
| | s09 | [Agent 记忆系统](doc/doc_learn/s09_agent记忆系统.md) |
| | s10 | [系统提示词构建](doc/doc_learn/s10_系统提示词构建.md) |
| | s11 | [错误恢复](doc/doc_learn/s11_错误恢复.md) |
| 任务与调度 | s12 | [任务系统（持久化）](doc/doc_learn/s12_任务系统.md) |
| | s13 | [后台任务](doc/doc_learn/s13_后台任务.md) |
| | s14 | [定时调度（Cron）](doc/doc_learn/s14_定时调度.md) |
| 多 Agent 协作 | s15 | [Agent 团队协作](doc/doc_learn/s15_Agent团队协作.md) |
| | s16 | [团队协议](doc/doc_learn/s16_Agent团队协议.md) |
| | s17 | [团队自主代理](doc/doc_learn/s17_团队自主代理.md) |
| | s18 | [工作树与任务隔离](doc/doc_learn/s18_工作树与任务隔离.md) |
| 扩展进阶 | s19 | [MCP 与插件系统](doc/doc_learn/s19_关于mcp与插件系统.md) |
| | s20 | [动态工作流](doc/doc_learn/s20_动态工作流.md) |

配套图片资源见 [`doc/images/`](doc/images/)。

---

## 许可

MIT
