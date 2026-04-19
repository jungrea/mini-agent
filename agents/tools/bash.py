"""
tools/bash —— shell 命令执行。

对应源 s_full.py 第 136–149 行。

说明：
    本模块内置的"危险命令黑名单"只是最后一道兜底——真正的准入控制
    在 permissions/ 的 BashSecurityValidator + PermissionManager 中完成。
    即便 permission 放行，这里仍会阻断最致命的几条。
"""

import subprocess

from ..core.config import (
    CONTEXT_TRUNCATE_CHARS,
    PERSIST_OUTPUT_TRIGGER_CHARS_BASH,
    WORKDIR,
)
from .persisted_output import maybe_persist_output


# 绝对禁止执行的命令片段（即使权限系统放行也会在这里被拦截）
_HARD_BLOCKED = ("rm -rf /", "sudo", "shutdown", "reboot", "> /dev/")


def run_bash(command: str, tool_use_id: str = "") -> str:
    """
    在 WORKDIR 中执行一条 shell 命令并返回合并后的 stdout+stderr。

    参数：
        command:     完整 shell 命令字符串（shell=True，支持管道 / 重定向）
        tool_use_id: LLM 分配的调用 ID，用于大输出落盘

    返回：
        * 触发硬黑名单：直接返回 "Error: Dangerous command blocked"
        * 命令超时（120s）：返回 "Error: Timeout (120s)"
        * 无输出：返回 "(no output)"
        * 正常：合并 stdout+stderr，必要时落盘，最终按 CONTEXT_TRUNCATE_CHARS 截断

    注意 bash 的持久化阈值比通用工具更严格（30000 vs 50000），
    因为 shell 命令最容易产生海量低价值输出（进度条、日志、base64）。
    """
    if any(d in command for d in _HARD_BLOCKED):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        if not out:
            return "(no output)"

        out = maybe_persist_output(
            tool_use_id,
            out,
            trigger_chars=PERSIST_OUTPUT_TRIGGER_CHARS_BASH,
        )
        # 即便 marker 本身理论上不会超长，这里仍做兜底截断，防止异常情况
        return out[:CONTEXT_TRUNCATE_CHARS] if isinstance(out, str) else str(out)[:CONTEXT_TRUNCATE_CHARS]
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
