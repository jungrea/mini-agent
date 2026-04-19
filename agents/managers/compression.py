"""
managers/compression —— s06 上下文压缩。

对应源 s_full.py 第 287–350 行。

两层压缩策略：
    1) microcompact —— 轻量、无 LLM 调用：仅把"过旧的 tool_result"文字替换为占位符
    2) auto_compact —— 重度，需要 LLM 调用：把整段对话 LLM-summary 成 continuation 开头

microcompact 每轮主循环都跑（便宜），auto_compact 在 estimate_tokens 超阈值才触发。
"""

from __future__ import annotations

import json
import time

from ..core.config import (
    KEEP_RECENT,
    MODEL,
    PRESERVE_RESULT_TOOLS,
    TRANSCRIPT_DIR,
    client,
)


def estimate_tokens(messages: list) -> int:
    """
    极简 token 估算：JSON 字符数 // 4。

    这只是粗略估计，用来驱动"何时触发 auto_compact"的判断——
    真实 token 数会略有偏差，但对阈值比较足够。
    """
    return len(json.dumps(messages, default=str)) // 4


def microcompact(messages: list) -> None:
    """
    就地修改 messages：把过旧的大块 tool_result 文本替换成简短占位符。

    规则：
        * 只处理 role=user 且 content 是 list 的消息里的 tool_result 块
        * 最近 KEEP_RECENT 个（默认 3）不动
        * content < 100 字符的不动（已经很小了，压不出空间）
        * tool_name ∈ PRESERVE_RESULT_TOOLS（默认 {"read_file"}）不动
          —— 这些工具的返回值是"事实数据"，丢了模型会重复读导致死循环

    不会改变 messages 结构（不删元素），只替换文本内容，保证上游 LLM
    的 tool_use_id 对应关系不被破坏。
    """
    # 1) 先把所有 tool_result 块提出来（引用）
    tool_results = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_results.append(part)

    # 不够 KEEP_RECENT 个就不压了
    if len(tool_results) <= KEEP_RECENT:
        return

    # 2) 构建 tool_use_id → tool_name 映射（从 assistant 侧的 tool_use 块里取）
    tool_name_map: dict[str, str] = {}
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    # 注意这里是 SDK 对象（有 .type 属性），不是 dict
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_name_map[block.id] = block.name

    # 3) 压缩除最近 KEEP_RECENT 个之外的历史 tool_result
    for part in tool_results[:-KEEP_RECENT]:
        # content 非 str（可能是 list 多模态结构）或本身很小，跳过
        if not isinstance(part.get("content"), str) or len(part["content"]) <= 100:
            continue

        tool_id = part.get("tool_use_id", "")
        tool_name = tool_name_map.get(tool_id, "unknown")

        # read_file 这类"事实数据"工具豁免——丢了会让模型反复读
        if tool_name in PRESERVE_RESULT_TOOLS:
            continue

        part["content"] = f"[Previous: used {tool_name}]"


def auto_compact(messages: list, focus: str | None = None) -> list:
    """
    调 LLM 对整个对话做一次"总结式压缩"，返回新的 messages 起点。

    流程：
        1) 先把当前完整 messages 原封不动写到 .transcripts/transcript_<ts>.jsonl
           —— 关键：压缩是不可逆的信息丢失操作，必须先落盘留底
        2) 把 messages JSON 截断到前 80000 字符喂给 LLM，要求按 5 段结构总结：
             - 任务概述 / 当前状态 / 关键决策 / 下一步 / 需保留的上下文
        3) 把总结包装成一条 "continuation" user 消息返回——上层直接 messages[:] = 新list

    参数：
        messages: 整个对话历史（会被就地不变地保存到 transcript）
        focus:    可选，让 LLM 在总结时特别关注某个侧面（如"文件改动列表"）

    返回：
        新的 messages，只有 1 条 user 起点，形如：
            "This session is being continued from a previous conversation ..."
    """
    # --- 1) 落盘备份 ---
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")

    # --- 2) LLM 压缩 ---
    # 80000 字符兜底，防止对话本身就超过单次调用上限
    conv_text = json.dumps(messages, default=str)[:80000]

    # 5 段式总结 prompt：结构化让模型产出更稳定、更可复原
    prompt = (
        "Summarize this conversation for continuity. Structure your summary:\n"
        "1) Task overview: core request, success criteria, constraints\n"
        "2) Current state: completed work, files touched, artifacts created\n"
        "3) Key decisions and discoveries: constraints, errors, failed approaches\n"
        "4) Next steps: remaining actions, blockers, priority order\n"
        "5) Context to preserve: user preferences, domain details, commitments\n"
        "Be concise but preserve critical details.\n"
    )
    if focus:
        prompt += f"\nPay special attention to: {focus}\n"

    resp = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt + "\n" + conv_text}],
        max_tokens=4000,
    )
    summary = resp.content[0].text

    # --- 3) 包装成 continuation 起点消息 ---
    # 关键点：明确告诉模型"不要再向用户提问"，避免压缩后的第一轮就卡在 Q&A
    continuation = (
        "This session is being continued from a previous conversation that ran out "
        "of context. The summary below covers the earlier portion of the conversation.\n\n"
        f"{summary}\n\n"
        "Please continue the conversation from where we left it off without asking "
        "the user any further questions."
    )
    return [{"role": "user", "content": continuation}]
