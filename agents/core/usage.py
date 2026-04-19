"""
core/usage —— Token 用量追踪与 HUD 渲染。

为什么单独一个模块？
    * Anthropic SDK 每次 `messages.create()` 的返回对象里都带 `usage`，
      里面有精准的 `input_tokens` / `output_tokens`（以及 cache 命中/创建量），
      这是比 `managers/compression.estimate_tokens` 更权威的数据源。
    * agent_loop 每轮都应该把这份数据喂给 UsageTracker，累计 session 级指标；
      REPL 则只负责"渲染"——让数据流水线清晰：
           agent_loop → UsageTracker.record(usage) → repl.render_hud()

对外 API：
    * `UsageTracker`    —— session 级累计器，单例 `USAGE` 由本模块导出
    * `USAGE`           —— 进程级实例；agent_loop / repl 都从这里取
    * `format_hud(...)` —— 渲染成一行 HUD 文本（ctx 条形图 / cumulative / delta）

HUD 示例（3A 完整版）：
    ctx ███░░░░░░░ 12% · in 4,512 out 1,203 · total 5,715/100,000 · Δin 312 Δout 87
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import TOKEN_THRESHOLD


# HUD 在终端里的 ANSI 颜色。
#
# 之前试过 dim 白、以及青/亮白多色：在浅色背景终端（白底）上都会偏淡看不清。
# 当前方案：
#   * 标签 + 数值 + 分隔符 —— **统一黑色**（`\033[30m`）；无论深/浅背景都清晰
#   * 条形图              —— 按占用率三档变色（这个是"信息颜色"，保留）
#         ≤ 50%  绿色（安全）
#         ≤ 80%  黄色（注意）
#         >  80%  红色（即将触发 auto_compact）
#
# 注：纯黑在"纯黑背景"终端下也会看不见。但 macOS/Linux 常见配色（浅色主题 /
# Solarized / 默认 Terminal）都以浅色或中间色为背景，黑字最通吃；真·纯黑
# 背景的用户可自行把 `_ANSI_TEXT` 改成 `\033[37m`（白）或 `\033[0m`（终端默认）。
_ANSI_RESET: str = "\033[0m"
_ANSI_TEXT:  str = "\033[30m"     # black：标签 / 数值 / 分隔符统一用它
_ANSI_BAR_LOW:  str = "\033[32m"  # green
_ANSI_BAR_MID:  str = "\033[33m"  # yellow
_ANSI_BAR_HIGH: str = "\033[31m"  # red

# 条形图字符（10 格）
_BAR_FULL: str = "█"
_BAR_EMPTY: str = "░"
_BAR_WIDTH: int = 10


@dataclass
class UsageTracker:
    """
    Session 级 token 累计器。

    字段：
        total_input_tokens      —— 累计输入（含 cache_read + cache_creation + 纯 input）
        total_output_tokens     —— 累计输出
        cache_read_tokens       —— 累计 cache 命中（这部分是"便宜"的 input，单独记方便观察）
        cache_creation_tokens   —— 累计 cache 写入（首次写入价格最贵）
        last_input_tokens       —— 最近一轮输入增量（Δin）
        last_output_tokens      —— 最近一轮输出增量（Δout）
        last_total_prompt       —— 最近一轮的完整 prompt 大小
                                   （= input_tokens + cache_read + cache_creation，
                                    用于"ctx 占用"分母为 TOKEN_THRESHOLD 的进度条）
        turns                   —— 调用过多少轮 LLM（便于调试）

    本类只管累计与读取，不做网络 I/O，单元测试友好。
    """
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    last_input_tokens: int = 0
    last_output_tokens: int = 0
    last_total_prompt: int = 0
    turns: int = 0

    def record(self, usage: Any) -> None:
        """
        从 SDK 响应的 `response.usage` 读出字段并累计。

        SDK 的 usage 可能是 pydantic model，也可能是 dict；这里两种都兼容。
        字段缺失全部按 0 处理——teammate 子进程或测试替身可能不提供全字段。
        """
        if usage is None:
            return

        # 统一访问：既支持 obj.input_tokens，也支持 dict["input_tokens"]
        def _get(key: str) -> int:
            val = getattr(usage, key, None)
            if val is None and isinstance(usage, dict):
                val = usage.get(key)
            try:
                return int(val) if val is not None else 0
            except (TypeError, ValueError):
                return 0

        inp = _get("input_tokens")
        out = _get("output_tokens")
        cache_read = _get("cache_read_input_tokens")
        cache_create = _get("cache_creation_input_tokens")

        # 累计项
        self.total_input_tokens += inp + cache_read + cache_create
        self.total_output_tokens += out
        self.cache_read_tokens += cache_read
        self.cache_creation_tokens += cache_create

        # 本轮快照
        self.last_input_tokens = inp + cache_read + cache_create
        self.last_output_tokens = out
        self.last_total_prompt = inp + cache_read + cache_create

        self.turns += 1

    def reset(self) -> None:
        """重置全部计数，便于测试与 `/usage reset` 这类命令。"""
        self.__init__()  # type: ignore[misc]


# 进程级单例。agent_loop 和 repl 都 import 这一个。
USAGE: UsageTracker = UsageTracker()


# ============================================================================
# HUD 渲染
# ============================================================================

def _format_int(n: int) -> str:
    """千分位：`4512` → `4,512`。纯字符，不带颜色。"""
    return f"{n:,}"


def _ctx_bar(used: int, total: int, width: int = _BAR_WIDTH) -> tuple[str, float]:
    """
    画一个 width 格的进度条。返回 (bar_string, percent_float)。

    > 100% 时条形仍画满，百分比原样显示（例如 142%），提醒用户已越过阈值
    （auto_compact 会在下一轮触发）。
    """
    if total <= 0:
        return _BAR_EMPTY * width, 0.0
    pct = used / total
    filled = min(width, int(round(pct * width)))
    bar = _BAR_FULL * filled + _BAR_EMPTY * (width - filled)
    return bar, pct * 100.0


def _bar_color(pct: float) -> str:
    """
    按占用率返回条形图颜色：
        ≤ 50% 绿、≤ 80% 黄、> 80% 红。
    """
    if pct <= 50.0:
        return _ANSI_BAR_LOW
    if pct <= 80.0:
        return _ANSI_BAR_MID
    return _ANSI_BAR_HIGH


def _kv(label: str, value: str, color: bool) -> str:
    """渲染一个 `label value` 对；开颜色时 label 与 value 统一黑色。"""
    if not color:
        return f"{label} {value}"
    return f"{_ANSI_TEXT}{label} {value}{_ANSI_RESET}"


def format_hud(
    usage: UsageTracker = USAGE,
    color: bool = True,
    *,
    dim: bool | None = None,
) -> str:
    """
    渲染一行 HUD 文本。

    分母选用 `TOKEN_THRESHOLD`：与 auto_compact 触发阈值对齐，
    用户一眼就能看出"离自动压缩还有多远"。

    参数：
        usage: UsageTracker 实例，默认取全局单例
        color: 是否开启 ANSI 配色（文字统一黑色 + 条形三档绿/黄/红）；
               非 TTY 或测试场景传 False 可得到纯文本
        dim:   **已弃用**的别名，保留是为了兼容早期 `format_hud(dim=False)` 的调用。
               若显式传了 dim，它会覆盖 color。

    返回：
        单行字符串（含 ANSI 控制符时已 reset）；未调用过 LLM（turns == 0）时
        返回一条占位行，避免输出完全为空。
    """
    # 兼容旧参数：早期 API 用 dim 语义（"是否套暗色"），与"是否要颜色"
    # 在当时是同一件事；这里把两者都尊重一下，dim 优先级更高。
    if dim is not None:
        color = dim

    # 分隔符 "·"：与标签/数值同色（统一黑）
    sep = f" {_ANSI_TEXT}·{_ANSI_RESET} " if color else " · "

    # 未调用过 LLM 的占位。
    if usage.turns == 0:
        body = "ctx 0% · (no LLM call yet)"
        return f"{_ANSI_TEXT}{body}{_ANSI_RESET}" if color else body

    bar, pct = _ctx_bar(usage.last_total_prompt, TOKEN_THRESHOLD)

    # 拼 ctx 段：标签/百分比黑色 + 条形按占用率三档变色
    if color:
        bar_colored = f"{_bar_color(pct)}{bar}{_ANSI_RESET}"
        ctx_part = (
            f"{_ANSI_TEXT}ctx{_ANSI_RESET} "
            f"{bar_colored} "
            f"{_ANSI_TEXT}{pct:4.1f}%{_ANSI_RESET}"
        )
    else:
        ctx_part = f"ctx {bar} {pct:4.1f}%"

    # cumulative 段：in / out
    cum_part = _kv("in", _format_int(usage.total_input_tokens), color) + " " + \
               _kv("out", _format_int(usage.total_output_tokens), color)

    # 本轮 prompt 绝对大小段
    total_part = _kv(
        "total",
        f"{_format_int(usage.last_total_prompt)}/{_format_int(TOKEN_THRESHOLD)}",
        color,
    )

    # 本轮增量段：Δin / Δout
    delta_part = _kv("Δin", _format_int(usage.last_input_tokens), color) + " " + \
                 _kv("Δout", _format_int(usage.last_output_tokens), color)

    return sep.join([ctx_part, cum_part, total_part, delta_part])


__all__ = ["UsageTracker", "USAGE", "format_hud"]
