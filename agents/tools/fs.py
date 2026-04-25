"""
tools/fs —— 文件系统类工具。

对应源 s_full.py 第 130–134、151–180 行。
    safe_path: 沙箱化路径（防越权）—— 以"当前活动工作区"为根
    run_read:  读文件（按行截断 + 可选 limit + 超大落盘）
    run_write: 整文件覆盖写
    run_edit:  精确文本替换（只替换首次出现）

工作区如何确定：
    通过 _active_workdir() 解析。优先级：
        1) core.runtime.CURRENT_WORKDIR ContextVar —— 由 webui session 在
           agent_loop 执行前 set，让"该会话的工具"绑定到用户选定的目录
        2) core.config.WORKDIR —— 全局回退（CLI / teammate / cron 等
           没有"会话上下文"的场景）

    这种解耦让 webui 的多会话可以各自指向不同目录，又对老调用方零影响。
"""

from __future__ import annotations

from pathlib import Path

from ..core.config import CONTEXT_TRUNCATE_CHARS, CURRENT_WORKDIR, WORKDIR
from .persisted_output import maybe_persist_output


def _active_workdir() -> Path:
    """
    返回当前活动工作区。

    * 在 webui worker 线程里：由 Session._run_one_round 在 agent_loop 调用
      前 set 入 ContextVar，返回该会话指定的目录
    * 其它一切场景（CLI、teammate 后台线程、cron 触发的子 agent...）：
      ContextVar 默认 None，回退全局 WORKDIR

    返回 Path 而不是 str —— 下游 (.../path).resolve() / is_relative_to 都要 Path
    """
    cw = CURRENT_WORKDIR.get()
    return cw if cw is not None else WORKDIR


def safe_path(p: str) -> Path:
    """
    把用户/模型提供的路径解析成 _active_workdir() 内的绝对路径，
    并拒绝路径越权（防 "../../../etc/passwd" 类穿越）。

    resolve() 会把 .. 展平，然后 is_relative_to(base) 判断最终位置
    是否仍在沙箱根目录下，不在则抛异常。
    """
    base = _active_workdir()
    path = (base / p).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, tool_use_id: str = "", limit: int | None = None) -> str:
    """
    读取文件内容。

    参数：
        path:         目标文件相对路径（相对当前活动工作区）
        tool_use_id:  LLM 侧工具调用 ID，用于可能的落盘
        limit:        最多返回多少行；超出时在末尾追加"... (N more)"

    返回：
        文件内容字符串；失败时返回 "Error: ..."（不抛异常——让 LLM 看到错误并自行处理）
    """
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        out = "\n".join(lines)

        # 超大文件：走 persisted_output 落盘 + 回传 marker
        out = maybe_persist_output(tool_use_id, out)

        # 最终兜底截断，防止 marker 本身还是过长
        return out[:CONTEXT_TRUNCATE_CHARS] if isinstance(out, str) else str(out)[:CONTEXT_TRUNCATE_CHARS]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    """
    整文件覆盖写。

    行为：
        * 自动创建父目录
        * 存在即覆盖，不做备份（教学脚本，保持最简）
    """
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """
    精确文本替换：把 old_text 在文件中的**首次出现**替换为 new_text。

    只替换一次是故意的——避免 "foo" 这样模糊的 old_text 批量误伤。
    LLM 被训练为使用更长的上下文片段来唯一锚定替换位置。
    """
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"
