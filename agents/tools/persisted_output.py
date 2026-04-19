"""
tools/persisted_output —— s06 大体积工具结果落盘。

对应源 learn-claude-code-main/agents/s_full.py 第 82–126 行。

设计意图：
    LLM 的工具调用返回值会被原样塞进 messages[].content[].tool_result，
    大文件读取 / 海量 shell 输出会让上下文迅速膨胀到数十万 token。
    本模块在"工具执行完成 → 回传给模型前"这一步，对过大的字符串做：
        1) 落盘到 .task_outputs/tool-results/<tool_use_id>.txt
        2) 用 <persisted-output> 标记包裹一小段预览 + 文件路径回传
    模型需要完整内容时可以再通过 read_file 读该文件。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.config import (
    TOOL_RESULTS_DIR,
    WORKDIR,
    PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT,
    PERSISTED_OPEN,
    PERSISTED_CLOSE,
    PERSISTED_PREVIEW_CHARS,
)


def _persist_tool_result(tool_use_id: str, content: str) -> Path:
    """
    把工具的完整返回字符串写到磁盘，返回相对 WORKDIR 的路径。

    参数：
        tool_use_id: LLM 侧分配的工具调用 ID，用作文件名
        content:     工具返回的完整字符串

    返回：
        落盘后的相对路径（便于回传给 LLM 时显示得短小整洁）
    """
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # tool_use_id 有可能包含奇怪字符，做一次清洗防止写到意料外的路径
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", tool_use_id or "unknown")
    path = TOOL_RESULTS_DIR / f"{safe_id}.txt"

    # 同一 tool_use_id 幂等：已存在则不覆盖（避免极端情况下 hash 冲突丢内容）
    if not path.exists():
        path.write_text(content)
    return path.relative_to(WORKDIR)


def _format_size(size: int) -> str:
    """把字节数格式化为人类可读字符串（B / KB / MB）。"""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _preview_slice(text: str, limit: int) -> tuple[str, bool]:
    """
    从 text 开头截取不超过 limit 字符的预览。

    关键：尽量在"换行符"处切，避免把一行日志切成两半。
    做法是——在前 limit 字符里找最后一个换行符位置 idx；
    只有当 idx > limit*0.5（保证我们没有为了对齐而丢掉一半内容）时才使用它，
    否则直接硬切到 limit。

    返回：(preview, has_more)
        has_more: True 表示原文确实被截断了，marker 里会补"..."尾巴
    """
    if len(text) <= limit:
        return text, False
    idx = text[:limit].rfind("\n")
    cut = idx if idx > (limit * 0.5) else limit
    return text[:cut], True


def _build_persisted_marker(stored_path: Path, content: str) -> str:
    """
    构造回传给 LLM 的 marker 文本。

    形如：
        <persisted-output>
        Output too large (1.2MB). Full output saved to: .task_outputs/tool-results/xxx.txt

        Preview (first 2.0KB):
        <预览内容>
        ...
        </persisted-output>
    """
    preview, has_more = _preview_slice(content, PERSISTED_PREVIEW_CHARS)
    marker = (
        f"{PERSISTED_OPEN}\n"
        f"Output too large ({_format_size(len(content))}). "
        f"Full output saved to: {stored_path}\n\n"
        f"Preview (first {_format_size(PERSISTED_PREVIEW_CHARS)}):\n"
        f"{preview}"
    )
    if has_more:
        marker += "\n..."
    marker += f"\n{PERSISTED_CLOSE}"
    return marker


def maybe_persist_output(
    tool_use_id: str,
    output: str,
    trigger_chars: int | None = None,
) -> str:
    """
    工具层统一的"是否需要落盘"入口。

    参数：
        tool_use_id:   LLM 分配的调用 ID
        output:        工具原始返回值（未必是 str，这里兜底转 str）
        trigger_chars: 触发阈值；None 表示用通用默认值（50000）；
                       bash 工具传入更严格的 30000。

    返回：
        * 长度未超阈值：原样返回
        * 超阈值：返回 <persisted-output> 包裹的 marker 文本
    """
    if not isinstance(output, str):
        return str(output)

    trigger = (
        PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT
        if trigger_chars is None
        else int(trigger_chars)
    )
    if len(output) <= trigger:
        return output

    stored_path = _persist_tool_result(tool_use_id, output)
    return _build_persisted_marker(stored_path, output)
