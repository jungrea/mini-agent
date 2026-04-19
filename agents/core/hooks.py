"""
core/hooks —— s08 hook system 的 mini 版融合实现。

对应源 learn-claude-code/agents/s08_hook_system.py（参考思想），
与本项目的 PermissionManager（s07）并列：权限在前、hook 在后，
两者不替代、不打架——权限是代码内置的安全底线，hook 是用户
自定义的外部扩展层。

-----------------------------------------------------------------
退出码契约（跨语言协议，与原版 s08 一致）
-----------------------------------------------------------------
    exit 0  → continue：一切照常
    exit 1  → block   ：阻止本次工具执行，stderr 作为 block_reason
    exit 2  → inject  ：允许执行，但把 stderr 作为补充信息注入到 tool_result

-----------------------------------------------------------------
环境变量（传给子进程）
-----------------------------------------------------------------
    HOOK_EVENT        事件名：SessionStart / PreToolUse / PostToolUse
    HOOK_TOOL_NAME    工具名（SessionStart 下为空字符串）
    HOOK_TOOL_INPUT   工具输入的 JSON 字符串
    HOOK_TOOL_OUTPUT  工具输出（仅 PostToolUse 下有值）

-----------------------------------------------------------------
可选结构化 stdout（JSON）
-----------------------------------------------------------------
如果 hook 的 stdout 恰好是合法 JSON 对象，支持两个字段：
    updatedInput       dict   → 仅 PreToolUse 有效，覆盖原 tool_input
    additionalContext  str    → 追加到 tool_result（等价 exit 2 + stderr）

刻意不支持 `permissionDecision` —— 权限决定权留给 PermissionManager，
避免两个子系统在 allow/deny/ask 上语义打架（本项目取舍 2A）。

-----------------------------------------------------------------
信任模型（本项目取舍 1B：默认开、一键关）
-----------------------------------------------------------------
    .hooks.json 不存在       → HookManager 空转（零成本）
    .hooks.json 存在         → 默认启用
    .hooks.disabled 存在     → 配置保留但跳过执行（长期静音）
    /hooks off               → 运行时临时禁用（进程级，不落盘）

与原版 s08 的 `.claude/.claude_trusted` 不同——mini 版假设这是"你自己的
教学仓库"，默认信任更友好；生产场景请回退到 s08 原版的显式 trust marker。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 支持的 hook 事件。
#:     SessionStart / PreToolUse / PostToolUse —— 对齐 s08 教学版原语
#:     RoundEnd（本项目扩展）—— 每轮 agent_loop 自然结束时触发一次，
#:                             可用于"每轮对话结束后做一次审计/记录"
HOOK_EVENTS: tuple[str, ...] = ("SessionStart", "PreToolUse", "PostToolUse", "RoundEnd")

#: 单条 hook 执行的硬超时（秒）。s08 教学版也是 30s；超时按 continue 处理并告警。
HOOK_TIMEOUT: int = 30

#: 默认配置文件名。放在工作区根目录（cwd）。
_DEFAULT_CONFIG_NAME: str = ".hooks.json"

#: 运行时禁用标记文件。与 .hooks.json 同级；存在即跳过执行。
_DISABLE_MARKER_NAME: str = ".hooks.disabled"


# ---------------------------------------------------------------------------
# HookManager
# ---------------------------------------------------------------------------

class HookManager:
    """
    外部 hook 加载与执行器。

    生命周期：
        * REPL 启动时构造一次，传入工作区根目录
        * 每次 run_hooks() 按需读取 self._config（reload 后刷新）
        * 进程退出自然销毁，无需 close

    设计要点：
        * 标准库 only：subprocess + json + pathlib，便于教学
        * 无 .hooks.json → enabled()=False → run_hooks 直接返回空结果
        * hook 的 stdout 尝试解析为 JSON；非 JSON 按普通日志忽略（不报错）
        * hook 异常（超时、非零 exit、语法错）永远不让主 agent 循环崩溃
    """

    def __init__(self,
                 workdir: str | os.PathLike[str] | None = None,
                 config_name: str = _DEFAULT_CONFIG_NAME) -> None:
        self.workdir: Path = Path(workdir or os.getcwd()).resolve()
        self.config_name: str = config_name
        self._config_path: Path = self.workdir / config_name
        self._disable_path: Path = self.workdir / _DISABLE_MARKER_NAME
        #: 运行时关闭开关（/hooks off），独立于磁盘 disable marker
        self._runtime_off: bool = False
        #: 已加载的配置；reload() 会刷新
        self._config: dict[str, list[dict[str, Any]]] = {}
        self._load()

    # ---- 加载 / 开关 -------------------------------------------------------

    def _load(self) -> None:
        """读取 .hooks.json；文件不存在或格式错 → 空配置 + 警告（不抛异常）。"""
        if not self._config_path.exists():
            self._config = {}
            return
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[hooks] failed to load {self._config_path}: {e}")
            self._config = {}
            return

        hooks = raw.get("hooks") if isinstance(raw, dict) else None
        if not isinstance(hooks, dict):
            print(f"[hooks] {self.config_name} has no 'hooks' object; ignored")
            self._config = {}
            return

        # 只保留已知事件名，其余丢弃（提前发现 typo）
        cleaned: dict[str, list[dict[str, Any]]] = {}
        for event, entries in hooks.items():
            if event not in HOOK_EVENTS:
                print(f"[hooks] unknown event '{event}' ignored "
                      f"(supported: {', '.join(HOOK_EVENTS)})")
                continue
            if not isinstance(entries, list):
                print(f"[hooks] event '{event}' must be a list; ignored")
                continue
            cleaned[event] = [e for e in entries if isinstance(e, dict) and "command" in e]
        self._config = cleaned

    def reload(self) -> int:
        """重新读配置；返回加载到的 hook 条目总数（跨所有事件）。"""
        self._load()
        return sum(len(v) for v in self._config.values())

    def enabled(self) -> bool:
        """当前是否会真正执行 hook。"""
        if self._runtime_off:
            return False
        if self._disable_path.exists():
            return False
        return bool(self._config)

    def disable(self) -> None:
        """/hooks off：运行时禁用，不改磁盘。"""
        self._runtime_off = True

    def enable(self) -> None:
        """/hooks on：解除运行时禁用（磁盘 marker 仍生效）。"""
        self._runtime_off = False

    # ---- 查询 --------------------------------------------------------------

    def list_hooks(self) -> list[dict[str, Any]]:
        """
        返回扁平列表，供 /hooks 菜单展示。每项：
            {"event": "...", "matcher": "...", "command": "..."}
        """
        rows: list[dict[str, Any]] = []
        for event in HOOK_EVENTS:
            for entry in self._config.get(event, []):
                rows.append({
                    "event": event,
                    "matcher": entry.get("matcher", "*"),
                    "command": entry.get("command", ""),
                })
        return rows

    def status_line(self) -> str:
        """一行状态，给 SessionStart 打印 + /hooks 使用。"""
        if not self._config_path.exists():
            return f"[hooks] no {self.config_name}; extension layer idle"
        if self._disable_path.exists():
            return f"[hooks] disabled via {_DISABLE_MARKER_NAME}"
        if self._runtime_off:
            return "[hooks] runtime off (use /hooks on to re-enable)"
        counts = {e: len(self._config.get(e, [])) for e in HOOK_EVENTS}
        parts = ", ".join(f"{k}={v}" for k, v in counts.items())
        return f"[hooks] loaded from {self.config_name} ({parts})"

    # ---- 执行 --------------------------------------------------------------

    def run_hooks(self, event: str, context: dict[str, Any]) -> dict[str, Any]:
        """
        触发指定事件的所有匹配 hook。

        context 的期望字段：
            * PreToolUse / PostToolUse : tool_name, tool_input (dict)
            * PostToolUse (额外)       : tool_output (str)
            * SessionStart             : 无要求
            * RoundEnd                 : 可选 round_rounds / stop_reason / last_assistant_text
                                         （都会以字符串形式塞到 HOOK_* 环境变量里供脚本使用）

        返回：
            {
                "blocked": bool,              # 任一 hook exit 1
                "block_reason": str | None,
                "messages": list[str],        # inject 文本（exit 2 的 stderr 或 JSON additionalContext）
                "updated_input": dict | None, # 仅 PreToolUse：最后一个提供 updatedInput 的 hook 生效
            }
        """
        blank: dict[str, Any] = {"blocked": False, "block_reason": None,
                                 "messages": [], "updated_input": None}

        if not self.enabled() or event not in HOOK_EVENTS:
            return blank

        entries = self._config.get(event, [])
        if not entries:
            return blank

        tool_name = str(context.get("tool_name", ""))
        tool_input = context.get("tool_input", {}) or {}
        tool_output = context.get("tool_output", "")

        result = dict(blank)
        result["messages"] = []

        env = os.environ.copy()
        env["HOOK_EVENT"] = event
        env["HOOK_TOOL_NAME"] = tool_name
        try:
            env["HOOK_TOOL_INPUT"] = json.dumps(tool_input, ensure_ascii=False)
        except TypeError:
            env["HOOK_TOOL_INPUT"] = str(tool_input)
        env["HOOK_TOOL_OUTPUT"] = str(tool_output) if tool_output is not None else ""

        for entry in entries:
            matcher = entry.get("matcher", "*")
            if not _matches(matcher, tool_name):
                continue

            cmd = entry.get("command", "")
            if not cmd:
                continue

            outcome = _run_single(cmd, env, cwd=self.workdir)

            # 收集 inject 消息：stderr（exit 2）或 JSON additionalContext
            if outcome["inject_text"]:
                result["messages"].append(outcome["inject_text"])

            if outcome["updated_input"] is not None and event == "PreToolUse":
                # 多个 hook 都改写时，后者覆盖前者——教学版取最简语义
                result["updated_input"] = outcome["updated_input"]

            if outcome["blocked"]:
                result["blocked"] = True
                result["block_reason"] = outcome["block_reason"] or f"hook blocked: {cmd}"
                # 一旦被 block，后面同事件的 hook 不再执行
                break

        return result


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _matches(matcher: str, tool_name: str) -> bool:
    """
    matcher 语义（对齐 s08）：
        "*" / "" / 缺省 → 匹配所有（含 SessionStart 的空工具名）
        其它            → 大小写不敏感的精确匹配
    """
    if matcher in ("*", "", None):
        return True
    return matcher.lower() == tool_name.lower()


def _run_single(command: str,
                env: dict[str, str],
                cwd: Path) -> dict[str, Any]:
    """
    执行单条 hook 命令，统一成标准结果字典：
        {"blocked": bool, "block_reason": str|None,
         "inject_text": str|None, "updated_input": dict|None}

    所有异常都在这里吞掉，保证 agent_loop 绝对不会因 hook 故障崩溃。
    """
    result: dict[str, Any] = {
        "blocked": False,
        "block_reason": None,
        "inject_text": None,
        "updated_input": None,
    }

    try:
        proc = subprocess.run(
            command,
            shell=True,
            env=env,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"[hooks] timeout after {HOOK_TIMEOUT}s: {command!r} (treated as continue)")
        return result
    except OSError as e:
        print(f"[hooks] failed to spawn: {command!r}: {e}")
        return result

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    # 1) 优先解析结构化 stdout（JSON）
    if stdout.startswith("{") and stdout.endswith("}"):
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            upd = data.get("updatedInput")
            if isinstance(upd, dict):
                result["updated_input"] = upd
            ctx = data.get("additionalContext")
            if isinstance(ctx, str) and ctx:
                result["inject_text"] = ctx
            # 明确忽略 permissionDecision（本项目取舍 2A）
            if "permissionDecision" in data:
                print("[hooks] 'permissionDecision' is ignored by this mini impl; "
                      "use PermissionManager rules instead")

    # 2) 退出码契约
    if proc.returncode == 0:
        return result
    if proc.returncode == 2:
        # inject：把 stderr 作为补充信息；若 JSON 已给 additionalContext，则 stderr 追加在后
        extra = stderr or ""
        if extra:
            result["inject_text"] = (
                (result["inject_text"] + "\n" + extra) if result["inject_text"] else extra
            )
        return result

    # 非 0/2 → block（s08 规范：通常是 1，但任意非零都保守处理为 block）
    result["blocked"] = True
    result["block_reason"] = stderr or f"hook exited with {proc.returncode}"
    return result
