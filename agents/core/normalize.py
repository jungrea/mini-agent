"""
core/normalize —— 发给 Anthropic API 之前的最后一道结构安检。

定位（重要）：
    这是 **安全网**，不是 **bug 修复**。
    - 写 messages 的所有源头都应保证结构合法（loop.py / session.py / subagent.py 等）
    - 安全网负责把"漏网"的小错误降级为"轻度失败"，而不是让 SDK 直接抛 400
    - 一旦它真的修补了什么，就在 stderr 打一行简短日志，便于回溯到根因

参考：
    /Users/chenjun/Desktop/work/learn-claude-code-main/agents/s02_tool_use.py
    被注释掉的旧版 normalize_messages —— 思想一致，本实现增加了"块顺序"维度
    （旧版只查存在性，不查 tool_result 是否紧跟 tool_use）。

Anthropic messages 几条硬规则（违反任一即 400）：
    R1. 每个 assistant 里的 tool_use_id 必须在 **后续某条** user 消息里有对应
        tool_result。
    R2. 当上一条 assistant 含 tool_use 时，**紧接的 user 消息必须以 tool_result
        作为开头块**——可以多个 tool_result，但不允许 text/任何其它块插队到
        tool_result 前面。（实际 SDK 表现：只要 tool_result 块都在 user content
        列表的"前段"且配齐，text 块放后面是合法的。）
    R3. user 与 assistant 必须严格交替，不能两条 user 或两条 assistant 相邻。
    R4. content 不能为空字符串、不能为空列表（API 会 400 "must contain at least
        one block"）。

本模块解决 R1（补 placeholder）+ R2（重排）+ R4（空兜底），不处理 R3。
R3 在本项目里出现就是 loop 自己的 bug，应当让它直接 400 暴露。
"""

from __future__ import annotations

import sys
from typing import Any


def _content_blocks(msg: dict[str, Any]) -> list[Any] | None:
    """msg.content 是 list 时返回 list，是 str 或其它时返回 None。"""
    c = msg.get("content")
    return c if isinstance(c, list) else None


def _is_block(b: Any, btype: str) -> bool:
    return isinstance(b, dict) and b.get("type") == btype


def _strip_internal(block: Any) -> Any:
    """剥掉以 _ 开头的内部字段（API 不认识，多发会增加 token / 引发警告）。"""
    if not isinstance(block, dict):
        return block
    return {k: v for k, v in block.items() if not (isinstance(k, str) and k.startswith("_"))}


def normalize_messages(messages: list[dict[str, Any]],
                       verbose: bool = True) -> list[dict[str, Any]]:
    """
    返回一个**新的** messages 列表，原 list 不被改动。

    流程：
        1) 先做一遍浅拷贝 + 剥内部字段。SDK ContentBlock 对象（assistant.content
           里的）原样保留——SDK 序列化时会自己处理。
        2) 重排 user 消息里的 content 块：tool_result 在前、其余在后（修 R2）。
        3) 扫缺失 tool_result：assistant 里出现过的 tool_use_id 若整条 messages
           都没有对应 tool_result，则在末尾追加一条 user 消息补 placeholder（修 R1）。
        4) 兜底空 content：换成 "(empty)"（修 R4）。

    verbose=True 时，凡是真的修了什么，stderr 打一行 [normalize] xxx。
    """
    # ---- 1) 浅拷贝 + 剥内部字段 -------------------------------------------
    cleaned: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict) or not m.get("role"):
            continue
        new = {"role": m["role"]}
        c = m.get("content")
        if isinstance(c, list):
            new["content"] = [_strip_internal(b) for b in c]
        else:
            new["content"] = c
        cleaned.append(new)

    # ---- 2) 重排 user 里的块：tool_result 在前 ----------------------------
    reordered = 0
    for m in cleaned:
        if m["role"] != "user":
            continue
        blocks = _content_blocks(m)
        if not blocks:
            continue
        # 只在确实包含 tool_result 时才考虑重排——避免无谓改 user 输入
        has_tr = any(_is_block(b, "tool_result") for b in blocks)
        if not has_tr:
            continue
        # 稳定分桶：先 tool_result 后其它
        results = [b for b in blocks if _is_block(b, "tool_result")]
        others = [b for b in blocks if not _is_block(b, "tool_result")]
        new_blocks = results + others
        if new_blocks != blocks:
            m["content"] = new_blocks
            reordered += 1
    if verbose and reordered:
        print(f"[normalize] reordered {reordered} user message(s) "
              f"to put tool_result first", file=sys.stderr)

    # ---- 3) 补缺失的 tool_result placeholder ------------------------------
    existing: set[str] = set()
    needed: list[tuple[int, str]] = []   # (assistant_msg_index, tool_use_id)
    for i, m in enumerate(cleaned):
        blocks = _content_blocks(m)
        if not blocks:
            continue
        if m["role"] == "user":
            for b in blocks:
                if _is_block(b, "tool_result"):
                    tid = b.get("tool_use_id")
                    if tid:
                        existing.add(tid)
        elif m["role"] == "assistant":
            for b in blocks:
                # SDK ContentBlock：用 getattr 兜 dict / 对象两种
                btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                if btype == "tool_use":
                    bid = b.get("id") if isinstance(b, dict) else getattr(b, "id", None)
                    if bid:
                        needed.append((i, bid))

    missing = [(i, tid) for (i, tid) in needed if tid not in existing]
    if missing:
        if verbose:
            ids = ", ".join(tid for _, tid in missing)
            print(f"[normalize] inserted placeholder tool_result for orphan "
                  f"tool_use ids: {ids}", file=sys.stderr)
        # 全部补在末尾（合并成一条 user 消息——不会破坏交替，因为最后一条
        # 出问题的场景下末尾基本是 assistant）
        placeholder_blocks = [
            {"type": "tool_result", "tool_use_id": tid,
             "content": "(no result - cancelled or lost)"}
            for _, tid in missing
        ]
        # 若最后一条已经是 user 且含 list，就并进去；否则新建一条
        if cleaned and cleaned[-1]["role"] == "user" \
                and isinstance(cleaned[-1].get("content"), list):
            # placeholder 必须放在前面（R2）；现有 tool_result 也要保持在 text 前
            existing_results = [b for b in cleaned[-1]["content"]
                                if _is_block(b, "tool_result")]
            existing_others = [b for b in cleaned[-1]["content"]
                               if not _is_block(b, "tool_result")]
            cleaned[-1]["content"] = placeholder_blocks + existing_results + existing_others
        else:
            cleaned.append({"role": "user", "content": placeholder_blocks})

    # ---- 4) 兜底空 content ------------------------------------------------
    fixed_empty = 0
    for m in cleaned:
        c = m.get("content")
        if c == "" or c == [] or c is None:
            m["content"] = "(empty)"
            fixed_empty += 1
    if verbose and fixed_empty:
        print(f"[normalize] replaced {fixed_empty} empty content with placeholder",
              file=sys.stderr)

    return cleaned


__all__ = ["normalize_messages"]
