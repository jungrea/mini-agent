"""
team/teammate —— s11 团队成员管理器。

对应源 s_full.py 第 490–633 行，并集成 s07 权限控制。

核心能力：
    * spawn(name, role, prompt) —— 启动一个后台线程，让一个 LLM 以指定 role
      跑工具循环，带自己的 system_prompt
    * 两阶段循环（工作 / 空闲）：
        - 工作阶段：连续最多 50 轮工具调用，直到模型主动 idle 或 stop
        - 空闲阶段：每 POLL_INTERVAL 秒轮询一次收件箱 + 文件任务板，
          有消息或无主任务就 resume；IDLE_TIMEOUT 内无事可做 → shutdown
    * auto-claim：空闲阶段遇到 pending && no owner && no blockedBy 的任务，
      直接抢占、注入 <auto-claimed> 段，并在 messages 很短时重新注入 <identity>
      防止"上下文被压缩后 teammate 忘了自己是谁"
    * 权限检查：工具调用前走 PermissionManager；ask 在 teammate 非交互环境下
      **退化为 deny**，原因作为 tool_result 回传 LLM，让它自行换策略
"""

from __future__ import annotations

import json
import threading
import time

from ..core.config import (
    IDLE_TIMEOUT,
    MODEL,
    POLL_INTERVAL,
    TASKS_DIR,
    TEAM_DIR,
    WORKDIR,
    client,
)
from ..core.prompts import build_identity
from ..permissions.manager import PermissionManager
from ..tools.bash import run_bash
from ..tools.fs import run_edit, run_read, run_write
from .messaging import MessageBus


# teammate 的基础工具 schema（不走主循环的 TOOLS，保持 teammate 工具面更小）
_TEAMMATE_TOOLS: list[dict] = [
    {"name": "bash", "description": "Run command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Edit file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "send_message", "description": "Send message.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}}, "required": ["to", "content"]}},
    {"name": "idle", "description": "Signal no more work.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "claim_task", "description": "Claim task by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
]


class TeammateManager:
    """
    团队成员生命周期管理 + 单成员后台工具循环。

    持久化：
        .team/config.json：
            {"team_name": "default", "members": [{"name", "role", "status"}]}
    内存：
        self.threads: dict —— 当前登记的后台线程（spawn 时登记，目前未使用于回收）
    """

    def __init__(self, bus: MessageBus, task_mgr, perms: PermissionManager | None = None):
        """
        参数：
            bus:      消息总线（供 _loop 消费收件箱）
            task_mgr: TaskManager（auto-claim 时用）
            perms:    可选；提供则接入权限检查，teammate 下 ask 会退化为 deny
        """
        TEAM_DIR.mkdir(exist_ok=True)
        self.bus = bus
        self.task_mgr = task_mgr
        self.perms = perms  # 可为 None：此时保持源 s_full.py 的"无权限检查"行为
        self.config_path = TEAM_DIR / "config.json"
        self.config = self._load()
        self.threads: dict[str, threading.Thread] = {}

    # ------------------------------------------------------------------
    # 配置持久化
    # ------------------------------------------------------------------
    def _load(self) -> dict:
        """加载 .team/config.json；不存在则返回默认空配置。"""
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save(self) -> None:
        """整文件回写（indent=2 便于人类调试）。"""
        self.config_path.write_text(json.dumps(self.config, indent=2))

    def _find(self, name: str) -> dict | None:
        """线性查找成员；数量少不做字典优化。"""
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def _set_status(self, name: str, status: str) -> None:
        """更新成员状态并立即落盘，供外部 /team 查询看到实时状态。"""
        member = self._find(name)
        if member:
            member["status"] = status
            self._save()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def spawn(self, name: str, role: str, prompt: str) -> str:
        """
        启动一个 teammate。

        幂等约束：
            * 同名成员已存在且 status ∈ {working, idle, ...非 idle/shutdown} → 拒绝
            * status 为 idle 或 shutdown 的可以被"复活"（复用成员身份、重置状态到 working）
        """
        member = self._find(name)
        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            member["status"] = "working"
            member["role"] = role  # 允许顺便更新 role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        self._save()

        # 后台 daemon 线程：主 REPL 退出时这些线程会被直接终止
        t = threading.Thread(target=self._loop, args=(name, role, prompt), daemon=True)
        t.start()
        self.threads[name] = t
        return f"Spawned '{name}' (role: {role})"

    # ------------------------------------------------------------------
    # 权限适配：非交互环境下 ask → deny
    # ------------------------------------------------------------------
    def _check_permission(self, tool_name: str, tool_input: dict) -> tuple[bool, str]:
        """
        teammate 路径下的权限检查封装。

        返回 (allowed, reason)。
            * 无 perms 时：直接放行（保持原 s_full.py 行为）
            * allow：放行
            * ask：**退化为 deny**——teammate 跑在后台线程，input() 会抢主 REPL 输入
              或阻塞线程；非交互下把 ask 当 deny 是最安全的选择
            * deny：拒绝
        被拒绝时返回的 reason 会作为 tool_result 回传 LLM，让它自行调整策略。
        """
        if self.perms is None:
            return True, ""

        decision = self.perms.check(tool_name, tool_input)
        if decision["behavior"] == "allow":
            return True, ""
        # ask 在 teammate 非交互环境下退化为 deny
        return False, decision["reason"]

    # ------------------------------------------------------------------
    # 主循环（工作阶段 + 空闲阶段）
    # ------------------------------------------------------------------
    def _loop(self, name: str, role: str, prompt: str) -> None:
        """
        teammate 后台线程主体。

        状态机（大循环）：
            working (最多 50 轮工具调用) → idle（轮询等待）→ resume 到 working
                                                   ↘ shutdown (timeout 或收到 shutdown_request)
        """
        team_name = self.config["team_name"]

        # teammate 的系统提示：身份段走 core.prompts.build_identity（与 lead 共享措辞，
        # 最小耦合不整段走 builder——teammate 工具面更小、无需 CLAUDE.md/memory）。
        sys_prompt = build_identity(name=name, role=role, team=team_name, workdir=WORKDIR)
        messages: list = [{"role": "user", "content": prompt}]
        tools = _TEAMMATE_TOOLS

        while True:
            # =================== 工作阶段 =============================
            for _ in range(50):  # 最多连续 50 轮工具调用
                # (a) 处理收件箱：shutdown_request 立即退出；其它转成 user 消息
                inbox = self.bus.read_inbox(name)
                for msg in inbox:
                    if msg.get("type") == "shutdown_request":
                        self._set_status(name, "shutdown")
                        return
                    messages.append({"role": "user", "content": json.dumps(msg)})

                # (b) 调用 LLM
                try:
                    response = client.messages.create(
                        model=MODEL,
                        system=sys_prompt,
                        messages=messages,
                        tools=tools,
                        max_tokens=8000,
                    )
                except Exception:
                    # 网络/配额/鉴权错误都直接结束；教学版不做重试
                    self._set_status(name, "shutdown")
                    return

                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    # 模型没要调用工具，说明本轮想法已发完，退出内层
                    break

                # (c) 执行工具 + 权限检查
                results = []
                idle_requested = False
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    # 权限检查（对 idle / claim_task / send_message 这些业务协议工具
                    # 也走一次 check——它们通常不在 PermissionManager 的 WRITE_TOOLS 里，
                    # 默认 / auto 模式下会走到 allow 规则兜底。
                    allowed, reason = self._check_permission(block.name, block.input or {})
                    if not allowed:
                        output = f"Permission denied: {reason}"
                        print(f"  [{name}][DENIED] {block.name}: {reason}")
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": str(output)})
                        continue

                    # 放行：按业务语义分发
                    if block.name == "idle":
                        idle_requested = True
                        output = "Entering idle phase."
                    elif block.name == "claim_task":
                        output = self.task_mgr.claim(block.input["task_id"], name)
                    elif block.name == "send_message":
                        output = self.bus.send(name, block.input["to"], block.input["content"])
                    else:
                        # fs / bash 类工具：本地 dispatch 表
                        dispatch = {
                            "bash":       lambda **kw: run_bash(kw["command"]),
                            "read_file":  lambda **kw: run_read(kw["path"]),
                            "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
                            "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
                        }
                        output = dispatch.get(block.name, lambda **kw: "Unknown")(**block.input)

                    print(f"  [{name}] {block.name}: {str(output)[:120]}")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": str(output)})

                messages.append({"role": "user", "content": results})

                if idle_requested:
                    # 主动 idle：跳出工作阶段进入空闲阶段
                    break

            # =================== 空闲阶段 =============================
            self._set_status(name, "idle")
            resume = False

            # 总计等待 IDLE_TIMEOUT 秒，每 POLL_INTERVAL 秒 poll 一次
            for _ in range(IDLE_TIMEOUT // max(POLL_INTERVAL, 1)):
                time.sleep(POLL_INTERVAL)

                # (a) 有新消息：立刻 resume
                inbox = self.bus.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        if msg.get("type") == "shutdown_request":
                            self._set_status(name, "shutdown")
                            return
                        messages.append({"role": "user", "content": json.dumps(msg)})
                    resume = True
                    break

                # (b) 无消息：扫 unclaimed 任务；存在则抢 1 个并 resume
                unclaimed = []
                for f in sorted(TASKS_DIR.glob("task_*.json")):
                    t = json.loads(f.read_text())
                    if t.get("status") == "pending" and not t.get("owner") and not t.get("blockedBy"):
                        unclaimed.append(t)

                if unclaimed:
                    task = unclaimed[0]
                    self.task_mgr.claim(task["id"], name)

                    # identity 重注入：messages 太短说明上下文很可能刚被 auto_compact 过，
                    # 模型可能忘了自己是谁。阈值 3 对应"最多只剩 continuation + 少量交互"。
                    if len(messages) <= 3:
                        messages.insert(0, {"role": "user", "content":
                            f"<identity>You are '{name}', role: {role}, team: {team_name}.</identity>"})
                        messages.insert(1, {"role": "assistant", "content":
                            f"I am {name}. Continuing."})

                    # 把抢到的任务作为"自动领取"段塞给模型
                    messages.append({"role": "user", "content":
                        f"<auto-claimed>Task #{task['id']}: {task['subject']}\n{task.get('description', '')}</auto-claimed>"})
                    messages.append({"role": "assistant", "content":
                        f"Claimed task #{task['id']}. Working on it."})
                    resume = True
                    break

            if not resume:
                # 超时仍无事可做：自行 shutdown
                self._set_status(name, "shutdown")
                return
            self._set_status(name, "working")

    # ------------------------------------------------------------------
    # 观察性 API
    # ------------------------------------------------------------------
    def list_all(self) -> str:
        """人类可读的成员清单（用于 REPL /team 命令）。"""
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> list[str]:
        """所有成员名字列表（broadcast 时用）。"""
        return [m["name"] for m in self.config["members"]]
