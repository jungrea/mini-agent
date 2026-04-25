"""
core/config —— 全局配置与常量。

对应源文件 learn-claude-code-main/agents/s_full.py 第 36–80 行：
  * 读取 .env
  * 构造 Anthropic client
  * WORKDIR 与一系列运行时落盘目录
  * 压缩 / 持久化 / 轮询 / 阈值常量
  * VALID_MSG_TYPES 与持久化标记字符串

本模块只做"配置源"，不依赖 agents 包内任何其它模块，放在依赖链最底层。
"""

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from anthropic import Anthropic
from dotenv import load_dotenv


# --- 1) 环境变量加载 --------------------------------------------------------

# override=True：允许 .env 覆盖当前 shell 环境，保证在不同终端中行为一致
load_dotenv(override=True)

# 使用兼容网关（ANTHROPIC_BASE_URL）时，anthropic SDK 会把 ANTHROPIC_AUTH_TOKEN
# 当成官方 API Key 发送，反而造成双重鉴权失败。这里主动清理。
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


# --- 2) 运行时根与 LLM 客户端 ----------------------------------------------

# 所有落盘操作、安全校验（路径逃逸检查）都以 WORKDIR 为根。
# 这里使用 Path.cwd() 而不是本文件路径——因为教学脚本的语义是
# "以调用者当前工作目录为沙箱根"，与 s_full.py 保持一致。
WORKDIR: Path = Path.cwd()

# === 会话级工作区上下文 ====================================================
#
# CURRENT_WORKDIR：调用方（webui Session worker）在 agent_loop 执行前
# 用 token = CURRENT_WORKDIR.set(Path(...)) 把当前会话的 workdir 写入
# Context；执行结束后 CURRENT_WORKDIR.reset(token) 还原。
#
# 工具层（fs / bash / search）通过 fs._active_workdir() 读取：
#   * 有值 → 用会话指定的 workdir（webui 多会话各自独立）
#   * 无值 → 回退 WORKDIR（CLI / teammate / cron 等无会话上下文场景）
#
# 默认值留 None 而不是 WORKDIR：避免 ContextVar 在模块加载时就持引用，
# 也方便 _active_workdir() 一处统一回退。
#
# 放在 config（而不是 runtime）是刻意的：
#   * config 是依赖链最底层（不 import 任何 agents 子模块），不会引入
#     "tools/fs → core/runtime → team/teammate → tools/bash → tools/fs"
#     这种循环 import
#   * WORKDIR 与 CURRENT_WORKDIR 语义相邻，放一起便于阅读
#
# 注意：用 Optional[Path] 而不是 Path | None，因为 ContextVar 的泛型参数
# 在 Python 3.9 也走运行时求值（不被 from __future__ import annotations 推迟），
# PEP 604 的 X | None 语法在 3.9 上 TypeError。本项目 README 标的是 3.10+，
# 但保留 3.9 兼容性几乎零成本，没必要因此踩雷。
CURRENT_WORKDIR: "ContextVar[Optional[Path]]" = ContextVar("CURRENT_WORKDIR", default=None)

# 官方 SDK：若 base_url=None，则走官方 Anthropic 端点
client: Anthropic = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))

# MODEL_ID 必须通过环境变量提供（缺失时直接抛 KeyError，fail-fast）
MODEL: str = os.environ["MODEL_ID"]


# --- 3) 落盘目录 ------------------------------------------------------------

# 团队协作：成员配置 + 收件箱
TEAM_DIR: Path = WORKDIR / ".team"
INBOX_DIR: Path = TEAM_DIR / "inbox"

# 持久化的"文件任务"（多智能体可抢占）
TASKS_DIR: Path = WORKDIR / ".tasks"

# 可加载技能目录（每个技能一个子目录，内含 SKILL.md）
SKILLS_DIR: Path = WORKDIR / "skills"

# auto_compact 前的完整对话备份（便于事后追溯）
TRANSCRIPT_DIR: Path = WORKDIR / ".transcripts"

# 大尺寸工具输出落盘根目录；tool-results 子目录按 tool_use_id 存放原文
TASK_OUTPUT_DIR: Path = WORKDIR / ".task_outputs"
TOOL_RESULTS_DIR: Path = TASK_OUTPUT_DIR / "tool-results"


# --- 4) 阈值与控制常量 ------------------------------------------------------

# 估算 token 数超过此阈值即触发 auto_compact
TOKEN_THRESHOLD: int = 100000

# teammate 空闲阶段轮询间隔（秒）
POLL_INTERVAL: int = 5

# teammate 空闲阶段最长等待时间（秒）；超时则自行 shutdown
IDLE_TIMEOUT: int = 60

# 大输出落盘阈值：通用工具（read_file/subagent）默认阈值
PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT: int = 50000

# bash 工具阈值**严于**通用：shell 输出里常有海量日志 / 进度条 / base64，
# 早一点落盘能显著降低上下文占用
PERSIST_OUTPUT_TRIGGER_CHARS_BASH: int = 30000

# 即使已落盘，回传给 LLM 的 marker 仍可能超长，这里做最终截断
CONTEXT_TRUNCATE_CHARS: int = 50000

# 持久化标记的包裹 tag；LLM 看到这对 tag 就知道"完整内容已落盘"
PERSISTED_OPEN: str = "<persisted-output>"
PERSISTED_CLOSE: str = "</persisted-output>"

# marker 中回传的预览片段字符数
PERSISTED_PREVIEW_CHARS: int = 2000

# microcompact 时保留最近 N 条 tool_result 不压缩（保持近端语义完整）
KEEP_RECENT: int = 3

# 即使超过 KEEP_RECENT，这里列出的工具仍保留原文（这些工具的返回值
# 本身就是"事实数据"，一旦丢失会让模型陷入重复读取循环）
PRESERVE_RESULT_TOOLS: frozenset = frozenset({"read_file"})


# --- 5) 消息协议 ------------------------------------------------------------

# MessageBus / team 协议层允许的 type 枚举
VALID_MSG_TYPES: frozenset = frozenset({
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_response",
})
