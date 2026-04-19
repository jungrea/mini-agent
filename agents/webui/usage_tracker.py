"""
webui/usage_tracker —— 每会话独立的 token 用量统计。

设计：agents.core.usage.USAGE 是进程级单例，多会话并发会串扰。
我们不改 agents，只在 webui 层做"调用前/后差分"：

    before = _snapshot_global()
    agent_loop(...)    # 内部会多轮调用 LLM，累加到 USAGE
    after = _snapshot_global()
    session_tracker.add_delta(after - before)

同时记录"最近一轮 prompt 总量"用于 ctx 百分比进度条——
由于一轮 agent_loop 内部可能跑多次 LLM 调用，last_total_prompt
取 after 的值（= 最后一次 LLM 调用的 prompt 大小，最接近真实 ctx）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.config import TOKEN_THRESHOLD
from ..core.usage import USAGE


@dataclass
class GlobalSnapshot:
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    last_total_prompt: int = 0
    turns: int = 0


def snapshot_global() -> GlobalSnapshot:
    """对 agents.core.usage.USAGE 拍快照。"""
    return GlobalSnapshot(
        total_input_tokens=USAGE.total_input_tokens,
        total_output_tokens=USAGE.total_output_tokens,
        cache_read_tokens=USAGE.cache_read_tokens,
        cache_creation_tokens=USAGE.cache_creation_tokens,
        last_total_prompt=USAGE.last_total_prompt,
        turns=USAGE.turns,
    )


@dataclass
class SessionUsage:
    """单会话累计的 token 用量（差分模式）。"""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    last_input_tokens: int = 0
    last_output_tokens: int = 0
    last_total_prompt: int = 0
    turns: int = 0

    def apply_diff(self, before: GlobalSnapshot, after: GlobalSnapshot) -> None:
        """
        把一次 agent_loop 期间 USAGE 的增量累加到本会话。
        last_total_prompt 取 after 的快照值而非累计——这是 ctx 百分比的分子。
        """
        d_input = after.total_input_tokens - before.total_input_tokens
        d_output = after.total_output_tokens - before.total_output_tokens
        d_cache_r = after.cache_read_tokens - before.cache_read_tokens
        d_cache_c = after.cache_creation_tokens - before.cache_creation_tokens
        d_turns = after.turns - before.turns

        self.total_input_tokens += max(0, d_input)
        self.total_output_tokens += max(0, d_output)
        self.cache_read_tokens += max(0, d_cache_r)
        self.cache_creation_tokens += max(0, d_cache_c)
        self.turns += max(0, d_turns)

        # 本轮本会话的增量
        self.last_input_tokens = max(0, d_input)
        self.last_output_tokens = max(0, d_output)
        # 最新 prompt 大小：用 after 的快照（= 最后一次 LLM 调用时 prompt 大小）
        self.last_total_prompt = after.last_total_prompt

    def to_dict(self) -> dict[str, Any]:
        pct = (self.last_total_prompt / TOKEN_THRESHOLD * 100.0) if TOKEN_THRESHOLD else 0.0
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "last_input_tokens": self.last_input_tokens,
            "last_output_tokens": self.last_output_tokens,
            "last_total_prompt": self.last_total_prompt,
            "turns": self.turns,
            "token_threshold": TOKEN_THRESHOLD,
            "ctx_percent": round(pct, 1),
        }

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]
