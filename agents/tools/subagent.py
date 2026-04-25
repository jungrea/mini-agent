"""
tools/subagent —— s04 子智能体。

对应源 s_full.py 第 220–256 行。

核心思想：
    主智能体可以通过 `task` 工具派发一个**隔离的小型工具循环**去完成
    某个子任务（例如"探索代码库"、"按规格写一批文件"）。子智能体只拿到
    一个精简的工具集，且不会把自己的工具调用写回主对话历史——只把最终的
    text summary 返回给主智能体。这样能：
        1) 限制工具权限（默认 Explore 只读）
        2) 避免污染主上下文
        3) 并行性良好（未来可换成线程池）
"""

from ..core.config import MODEL, client
from ..core.normalize import normalize_messages
from .bash import run_bash
from .fs import run_edit, run_read, run_write


def run_subagent(prompt: str, agent_type: str = "Explore") -> str:
    """
    在当前进程内启动一个子智能体处理 prompt，返回它最终的文本总结。

    参数：
        prompt:     子任务的起始 user prompt
        agent_type: "Explore" → 只读子集（bash + read_file）
                    其它（如 "general-purpose"）→ 追加 write_file / edit_file

    返回：
        子智能体最后一个响应里所有 text 块拼接而成的字符串；
        没有 text 输出时返回占位 "(no summary)"；极端失败返回 "(subagent failed)"

    实现要点：
        * 最多循环 30 轮（避免死循环）
        * 每轮都 stop_reason != "tool_use" 即终止
        * 每个工具结果强制截断到 50000 字符（子任务不享受落盘，保持简单）
    """
    # 基础工具集：Explore 模式下仅只读
    sub_tools = [
        {
            "name": "bash",
            "description": "Run command.",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
        {
            "name": "read_file",
            "description": "Read file.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    ]

    # 非 Explore 模式：追加写文件相关工具
    if agent_type != "Explore":
        sub_tools += [
            {
                "name": "write_file",
                "description": "Write file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "edit_file",
                "description": "Edit file.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        ]

    # 子智能体内部的工具分发表（注意：这里不接 persisted_output 落盘，
    # 因为子任务的工具结果不会外泄到主对话里）
    sub_handlers = {
        "bash":       lambda **kw: run_bash(kw["command"]),
        "read_file":  lambda **kw: run_read(kw["path"]),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    }

    sub_msgs: list = [{"role": "user", "content": prompt}]
    resp = None
    # 硬上限 30 轮：避免子智能体陷入工具死循环耗掉配额
    for _ in range(30):
        resp = client.messages.create(
            model=MODEL,
            messages=normalize_messages(sub_msgs),
            tools=sub_tools,
            max_tokens=8000,
        )
        sub_msgs.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            break

        # 收集本轮所有 tool_use 的执行结果
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                h = sub_handlers.get(b.name, lambda **kw: "Unknown tool")
                # 强制截断 50000 字符；子任务不走 persisted_output
                results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": str(h(**b.input))[:50000],
                })
        sub_msgs.append({"role": "user", "content": results})

    # 拼接最后一轮响应里所有 text 块作为 summary 返回
    if resp:
        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    return "(subagent failed)"
