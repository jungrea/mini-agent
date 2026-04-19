"""
core/reminders —— per-turn `<system-reminder>` 装配器。

为什么单独一个模块？
    s10 教学里最重要的一个区分是：**稳定的 system prompt** 和
    **每轮变化的提醒** 要分开。原项目里 `agent_loop` 把 todo nag
    写成了一条 `<reminder>Update your todos.</reminder>` 塞在
    tool_result 列表最前头——思想对，但 tag 名与 Claude Code 真实
    做法（`<system-reminder>`）不一致，而且零散在 loop 里不便扩展。

本模块把"每轮提醒"收敛到一个函数 `build_system_reminder(...)`：
    * 输入是"本轮发生了什么"（todo_nag / 额外文本）
    * 输出是一条 user 消息 dict，或 None（本轮没东西要提醒）
    * 调用方（`agent_loop`）只管拼接结果，不关心 tag 名、格式

不在本模块处理的：
    * `<inbox>` 收件箱消息        —— 它是"事件上下文"，不是"规则提醒"
    * `<background-results>` BG   —— 同上
    * `<identity>` teammate 身份  —— teammate 专用，在 team/teammate.py 里
    这些都保留原 tag，不迁移。**规则类提醒** 才走 `<system-reminder>`。
"""

from __future__ import annotations

from typing import Any


def build_system_reminder(
    todo_nag: bool = False,
    extra: str | None = None,
) -> dict[str, Any] | None:
    """
    构造一条 `<system-reminder>` 的 user 消息块，供 loop 插入 tool_result 列表。

    参数：
        todo_nag: True 表示"连续 N 轮未更新 TODO 且还有 open items"，
                  需要提醒模型调 TodoWrite。
        extra:    额外的一行提醒文本（未来如有其它规则可扩展；当前无调用方）。

    返回：
        * 不需要提醒时返回 None（loop 据此判空跳过）
        * 需要提醒时返回：
              {"type": "text", "text": "<system-reminder>...</system-reminder>"}
          注意返回的是 **tool_result 列表里的一个 text 块**，不是完整 user 消息。
          loop 会把它 insert(0, ...) 到 results 最前面。

    设计取舍：
        * 多条提醒用换行拼在同一个 <system-reminder> 里，而不是多个小块——
          对 LLM 而言一个显眼的提醒块比多个分散的更易注意到
        * tag 名固定为 `system-reminder`（对齐 Claude Code 真实做法，
          和本项目的 s10 教学预期）
    """
    parts: list[str] = []

    if todo_nag:
        # 与源项目 s_full.py 和 s10 原意保持一致：促使模型调 TodoWrite 同步状态
        parts.append("Update your todos.")

    if extra:
        parts.append(extra.strip())

    if not parts:
        return None

    body = "\n".join(parts)
    return {"type": "text", "text": f"<system-reminder>\n{body}\n</system-reminder>"}


__all__ = ["build_system_reminder"]
