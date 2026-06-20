"""
tools/bash —— shell 命令执行。

对应源 s_full.py 第 136–149 行。

说明：
    本模块内置的"危险命令黑名单"只是最后一道兜底——真正的准入控制
    在 permissions/ 的 BashSecurityValidator + PermissionManager 中完成。
    即便 permission 放行，这里仍会阻断最致命的几条。

工作区：
    cwd 走 fs._active_workdir()——webui session 指定了 workdir 时跑在那里，
    否则回退全局 WORKDIR。详见 tools/fs.py 模块说明。
"""

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from ..core.config import (
    CONTEXT_TRUNCATE_CHARS,
    PERSIST_OUTPUT_TRIGGER_CHARS_BASH,
)
from .fs import _active_workdir
from .persisted_output import maybe_persist_output


# 绝对禁止执行的命令片段（即使权限系统放行也会在这里被拦截）
_HARD_BLOCKED = ("rm -rf /", "sudo", "shutdown", "reboot", "> /dev/")
_POLL_INTERVAL = 0.1
_KILL_GRACE_SECONDS = 1.0


@dataclass
class ShellResult:
    stdout: str
    stderr: str
    returncode: Optional[int]
    timed_out: bool = False
    cancelled: bool = False

    @property
    def output(self) -> str:
        return (self.stdout + self.stderr).strip()


def _popen_kwargs(cwd: Path) -> dict:
    kwargs = {
        "shell": True,
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        proc.terminate()

    try:
        proc.wait(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass

    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        proc.kill()


def run_shell_command(
    command: str,
    *,
    cwd: Path,
    timeout: Union[float, int] = 120,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> ShellResult:
    proc = subprocess.Popen(command, **_popen_kwargs(cwd))
    deadline = time.monotonic() + timeout if timeout is not None else None

    while True:
        try:
            stdout, stderr = proc.communicate(timeout=_POLL_INTERVAL)
            return ShellResult(stdout or "", stderr or "", proc.returncode)
        except subprocess.TimeoutExpired:
            if cancel_check is not None and cancel_check():
                _kill_process_tree(proc)
                stdout, stderr = proc.communicate()
                return ShellResult(stdout or "", stderr or "", proc.returncode, cancelled=True)
            if deadline is not None and time.monotonic() >= deadline:
                _kill_process_tree(proc)
                stdout, stderr = proc.communicate()
                return ShellResult(stdout or "", stderr or "", proc.returncode, timed_out=True)


def run_bash(
    command: str,
    tool_use_id: str = "",
    timeout: Union[float, int] = 120,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> str:
    """
    在当前活动工作区中执行一条 shell 命令并返回合并后的 stdout+stderr。

    参数：
        command:     完整 shell 命令字符串（shell=True，支持管道 / 重定向）
        tool_use_id: LLM 分配的调用 ID，用于大输出落盘

    返回：
        * 触发硬黑名单：直接返回 "Error: Dangerous command blocked"
        * 命令超时：终止整棵子进程树，返回 "Error: Timeout (...)"
        * 外部取消：终止整棵子进程树，返回 "Error: Cancelled"
        * 无输出：返回 "(no output)"
        * 正常：合并 stdout+stderr，必要时落盘，最终按 CONTEXT_TRUNCATE_CHARS 截断

    注意 bash 的持久化阈值比通用工具更严格（30000 vs 50000），
    因为 shell 命令最容易产生海量低价值输出（进度条、日志、base64）。
    """
    if any(d in command for d in _HARD_BLOCKED):
        return "Error: Dangerous command blocked"
    try:
        result = run_shell_command(
            command,
            cwd=_active_workdir(),
            timeout=timeout,
            cancel_check=cancel_check,
        )
        out = result.output
        if result.cancelled:
            return "Error: Cancelled" + (f"\n{out}" if out else "")
        if result.timed_out:
            return f"Error: Timeout ({timeout}s)" + (f"\n{out}" if out else "")
        if not out:
            return "(no output)"

        out = maybe_persist_output(
            tool_use_id,
            out,
            trigger_chars=PERSIST_OUTPUT_TRIGGER_CHARS_BASH,
        )
        # 即便 marker 本身理论上不会超长，这里仍做兜底截断，防止异常情况
        return out[:CONTEXT_TRUNCATE_CHARS] if isinstance(out, str) else str(out)[:CONTEXT_TRUNCATE_CHARS]
    except Exception as e:
        return f"Error: {e}"
