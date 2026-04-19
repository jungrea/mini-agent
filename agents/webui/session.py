"""
webui/session —— 单个对话会话：独立 history、独立 perms、独立 worker 线程。

架构：
    前端 POST /sessions/:id/messages  →  Session.submit_user_message(text)
                                       →  input_queue.put(text)
    worker 线程 loop：
        text = input_queue.get()   # 阻塞
        history.append({user})
        events.publish(user_message)
        before = snapshot()
        agent_loop(history, perms, hooks)    # 中途的事件由补丁进的回调推送
        after = snapshot()
        usage.apply_diff(...)
        events.publish(usage / round_end)
        persist()

关键补丁：
    * agent_loop 内部不直接发 SSE 事件；我们通过"hook 补丁"+"monkey patch"
      两种方式都不优雅。更干净的做法是：
      —— 让 worker 在每轮结束后扫 history 新增的部分并 publish 对应事件
      由于 agent_loop 一轮内可能执行多次 LLM + 多个工具，前端用户体验要求
      每个工具结果出来就能看到；因此我们在 worker 里**把单轮拆成观察-发布**：
      agent_loop 返回时一次性 publish 历史尾部的所有新事件。对本地场景够用，
      实现简单、零侵入 agent_loop。

      （进一步的真·流式推送需要给 agent_loop 增加 on_event 回调；为保持
       最小侵入，当前版本只做"每轮末尾一次性推"——多个工具结果会同时出现，
       但响应体感仍优于等整轮完全结束——因为每条 user 消息触发的一轮
       agent_loop 本身就是"多个 LLM 往返"的整体。）

权限交互：
    perms = PermissionManager(mode, ask_callback=self._ask_user)
    _ask_user 通过 EventBus publish 一个 permission_ask 事件 + 挂一个
    threading.Event；前端通过 WebSocket resolve 后由 permission_bridge
    set() 该 Event。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from queue import Empty, Queue
from typing import Any, Optional

from ..core.hooks import HookManager
from ..core.loop import agent_loop
from ..core.runtime import TODO
from ..managers.compression import auto_compact
from ..permissions.manager import MODES, PermissionManager

from .config import PERMISSION_ASK_TIMEOUT, WORKER_POLL_INTERVAL
from .events import Event, EventBus, EventType, GLOBAL_BUS
from .usage_tracker import SessionUsage, snapshot_global


# ---------------- 消息 / content 的 JSON 序列化 ----------------

def serialize_content(content: Any) -> Any:
    """
    把 messages 里的 assistant.content（SDK 的 ContentBlock 列表）转成
    JSON 可序列化的 dict 列表。user 消息里的 tool_result dict 原样返回。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict):
                out.append(block)
                continue
            btype = getattr(block, "type", None)
            if btype == "text":
                out.append({"type": "text", "text": getattr(block, "text", "")})
            elif btype == "tool_use":
                out.append({
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": getattr(block, "input", {}) or {},
                })
            else:
                # 其他未知块类型（thinking / image 等）尽量转 dict
                out.append({"type": btype or "unknown", "repr": str(block)})
        return out
    return str(content)


def serialize_message(msg: dict) -> dict:
    return {"role": msg.get("role"), "content": serialize_content(msg.get("content"))}


# ---------------------------- Session ----------------------------

class Session:
    """
    单个对话会话。

    公开属性：
        id, title, mode, created_at, updated_at
        history: list[dict]          （agent_loop 要的原始格式）
        perms:   PermissionManager
        events:  EventBus            （SSE 订阅）
        usage:   SessionUsage
    """

    def __init__(self, id: str, title: str, mode: str = "default",
                 history: Optional[list] = None,
                 hooks: Optional[HookManager] = None) -> None:
        if mode not in MODES:
            mode = "default"
        self.id: str = id
        self.title: str = title
        self.mode: str = mode
        self.created_at: float = time.time()
        self.updated_at: float = time.time()

        self.history: list = history or []
        self.events: EventBus = EventBus()
        self.usage: SessionUsage = SessionUsage()
        self.hooks: Optional[HookManager] = hooks

        # 权限管理器：注入 ask_callback 把 ask 请求推到前端
        self.perms: PermissionManager = PermissionManager(
            mode=mode, ask_callback=self._ask_user_callback,
        )

        # 权限 ask 的异步同步桥接
        self._pending_asks: dict[str, dict] = {}  # ask_id -> {event, answer}
        self._asks_lock: threading.Lock = threading.Lock()

        # 取消标志：前端点"停止"按钮 → request_cancel() 置位；
        # agent_loop 在多处调用 is_cancel_requested() 检查并决定早停。
        # 每轮新消息开头会清掉上次的标志（避免"之前取消过，这次刚发消息就被取消"）。
        self._cancel_requested: threading.Event = threading.Event()

        # worker 线程 + input_queue
        self._input_queue: Queue = Queue()
        self._stop_event: threading.Event = threading.Event()
        self._worker: threading.Thread = threading.Thread(
            target=self._worker_loop, daemon=True, name=f"SessionWorker-{id[:8]}"
        )
        self._state: str = "idle"   # idle | running
        self._state_lock: threading.Lock = threading.Lock()

        self._worker.start()

    # ---------------- 对外 API ----------------

    def submit_user_message(self, text: str) -> None:
        """异步投递一条用户消息。worker 线程会接手驱动一轮 agent_loop。"""
        self._input_queue.put(("user", text))

    def submit_slash_result(self, text: str) -> None:
        """
        某些斜杠命令（如 /compact 由 LLM 主动触发、/clear hard）会"改 history 但不驱动循环"。
        这个方法用于把这类操作的文字结果作为一条 notice 广播到前端。
        """
        self.events.publish(Event(
            type=EventType.NOTICE, session_id=self.id,
            data={"level": "info", "text": text},
        ))

    def set_mode(self, mode: str) -> str:
        if mode not in MODES:
            return f"Unknown mode: {mode}"
        self.perms.mode = mode
        self.mode = mode
        self.updated_at = time.time()
        self.events.publish(Event(
            type=EventType.SESSION_UPDATED, session_id=self.id,
            data={"mode": mode},
        ))
        return f"Mode switched to {mode}"

    # ---------------- 取消 / 停止 ----------------

    def request_cancel(self) -> dict:
        """
        请求中止当前正在运行的一轮对话。幂等。

        语义：
            * 若当前 state == idle，什么都不做，返回 {"accepted": False, ...}
            * 若 state == running，置位取消标志；同时把 pending 的权限 ask
              按 deny 唤醒（避免 worker 卡在 threading.Event.wait）
            * agent_loop 在多处检查 cancel_check 回调：
                - 每轮开头
                - 每个 tool_use 处理前
              检测到后会立即返回，worker 状态回到 idle。
            * 一旦 LLM 调用已发出（client.messages.create 同步阻塞中），
              无法打断 Anthropic SDK 的请求；但该请求返回后不会再进入下一轮。
        """
        state = self.state()
        if state != "running":
            return {"accepted": False, "state": state, "reason": "not running"}

        self._cancel_requested.set()

        # 唤醒所有 pending 的权限 ask，按 deny 处理
        with self._asks_lock:
            pend_items = list(self._pending_asks.items())
            for ask_id, pend in pend_items:
                pend["answer"] = "deny"
                pend["event"].set()

        self.events.publish(Event(
            type=EventType.NOTICE, session_id=self.id,
            data={"level": "warn",
                  "text": "已请求停止。LLM 正在调用中时需等其返回；之后不再继续。"},
        ))
        return {"accepted": True, "state": state, "pending_asks_cleared": len(pend_items)}

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def _clear_cancel(self) -> None:
        """每次开始一轮新对话时，清掉上次的取消标志。内部用。"""
        self._cancel_requested.clear()

    def resolve_permission_ask(self, ask_id: str, decision: str) -> bool:
        """前端通过 WebSocket 回传决策。decision ∈ allow/deny/always。"""
        with self._asks_lock:
            pend = self._pending_asks.get(ask_id)
            if pend is None:
                return False
            pend["answer"] = decision if decision in ("allow", "deny", "always") else "deny"
            pend["event"].set()
        self.events.publish(Event(
            type=EventType.PERMISSION_RESOLVED, session_id=self.id,
            data={"ask_id": ask_id, "decision": pend["answer"]},
        ))
        return True

    def stop(self) -> None:
        # 请求 cancel，让 agent_loop 尽早返回
        self._cancel_requested.set()
        # 唤醒所有 pending 的权限 ask，避免 worker 卡死
        with self._asks_lock:
            for pend in self._pending_asks.values():
                pend["answer"] = "deny"
                pend["event"].set()
        self._stop_event.set()
        # 投一个哨兵把 worker 从 queue.get 里唤醒
        self._input_queue.put(("stop", ""))

    def state(self) -> str:
        with self._state_lock:
            return self._state

    def _set_state(self, s: str) -> None:
        with self._state_lock:
            self._state = s
        self.events.publish(Event(
            type=EventType.STATUS, session_id=self.id, data={"state": s},
        ))

    # ---------------- 序列化 ----------------

    def to_meta(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "mode": self.mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.history),
            "state": self.state(),
        }

    def to_dict_full(self) -> dict:
        return {
            **self.to_meta(),
            "history": [serialize_message(m) for m in self.history],
            "usage": self.usage.to_dict(),
        }

    # ---------------- 内部：权限回调 ----------------

    def _ask_user_callback(self, tool_name: str, tool_input: dict) -> str:
        """
        由 PermissionManager.ask_user 在 worker 线程中同步调用。

        流程：
            1) 生成 ask_id，注册 threading.Event
            2) publish permission_ask 事件给前端
            3) Event.wait(timeout)：前端 WS 回传后由 resolve_permission_ask 唤醒
            4) 读答案 → 返回 allow/deny/always
        超时或 stop_event 被 set 时按 deny 处理。
        """
        ask_id = uuid.uuid4().hex[:12]
        ev = threading.Event()
        with self._asks_lock:
            self._pending_asks[ask_id] = {"event": ev, "answer": "deny"}

        self.events.publish(Event(
            type=EventType.PERMISSION_ASK, session_id=self.id,
            data={
                "ask_id": ask_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "timeout_sec": PERMISSION_ASK_TIMEOUT,
            },
        ))

        # 等待前端或停止信号
        got = ev.wait(timeout=PERMISSION_ASK_TIMEOUT)
        with self._asks_lock:
            pend = self._pending_asks.pop(ask_id, None)
        if not got or pend is None:
            # 超时：推一条 notice 让用户知道
            self.events.publish(Event(
                type=EventType.NOTICE, session_id=self.id,
                data={"level": "warn",
                      "text": f"权限请求 {tool_name} 超时（{int(PERMISSION_ASK_TIMEOUT)}s），按拒绝处理"},
            ))
            return "deny"
        return pend["answer"]

    # ---------------- 内部：worker ----------------

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                kind, payload = self._input_queue.get(timeout=WORKER_POLL_INTERVAL)
            except Empty:
                continue
            if kind == "stop":
                break
            if kind == "user":
                self._run_one_round(payload)
            elif kind == "slash_compact":
                self._run_compact(payload)
            elif kind == "slash_clear":
                self._run_clear(payload)

    def _run_one_round(self, user_text: str) -> None:
        """驱动一轮 agent_loop，把新增事件推给前端。"""
        self._set_state("running")
        self.history.append({"role": "user", "content": user_text})
        # user_message 前端已本地回显，这里 marker 取 append 之后，避免再推一次
        marker = len(self.history)
        self.events.publish(Event(
            type=EventType.USER_MESSAGE, session_id=self.id,
            data={"content": user_text, "ts": time.time()},
        ))

        # agent_loop 通过 progress 回调实时告诉我们"现在在哪一步"。
        # 在这两个集合里分别记下"已经在 tool_start / tool_end 里推给前端的"
        # 之后 _publish_tail 扫到同 id 的 tool_use / tool_result 就跳过。
        pushed_tool_use_ids: set[str] = set()
        pushed_tool_result_ids: set[str] = set()

        def _progress(event: str, payload: dict) -> None:
            try:
                if event == "llm_start":
                    self.events.publish(Event(
                        type=EventType.PHASE, session_id=self.id,
                        data={"state": "thinking", "label": "LLM 思考中…"},
                    ))
                    self.events.publish(Event(
                        type=EventType.LLM_START, session_id=self.id, data={},
                    ))
                elif event == "llm_end":
                    self.events.publish(Event(
                        type=EventType.LLM_END, session_id=self.id,
                        data={"stop_reason": payload.get("stop_reason")},
                    ))
                elif event == "tool_start":
                    tid = payload.get("id", "")
                    pushed_tool_use_ids.add(tid)
                    self.events.publish(Event(
                        type=EventType.PHASE, session_id=self.id,
                        data={"state": "tool_running",
                              "label": f"执行工具 {payload.get('name')}…",
                              "tool_use_id": tid},
                    ))
                    # 直接推 tool_use 事件，前端立即渲染卡片
                    self.events.publish(Event(
                        type=EventType.TOOL_USE, session_id=self.id,
                        data={
                            "id": tid,
                            "name": payload.get("name"),
                            "input": payload.get("input") or {},
                            "status": "running",
                        },
                    ))
                    self.events.publish(Event(
                        type=EventType.TOOL_START, session_id=self.id, data=payload,
                    ))
                elif event == "tool_end":
                    tid = payload.get("id", "")
                    # 1) 先发 tool_end 事件（前端用来更新卡片状态为 ✓ 完成 · 耗时）
                    self.events.publish(Event(
                        type=EventType.TOOL_END, session_id=self.id,
                        data={
                            "id": tid,
                            "name": payload.get("name"),
                            "duration_ms": payload.get("duration_ms"),
                            "output_preview": payload.get("output_preview"),
                            "error": payload.get("error", False),
                        },
                    ))
                    # 2) 立即把完整 tool_result 推给前端（不等 _publish_tail）
                    output = payload.get("output", "")
                    if output:
                        pushed_tool_result_ids.add(tid)
                        self.events.publish(Event(
                            type=EventType.TOOL_RESULT, session_id=self.id,
                            data={
                                "tool_use_id": tid,
                                "content": output,
                            },
                        ))
                elif event == "tool_denied":
                    self.events.publish(Event(
                        type=EventType.TOOL_DENIED, session_id=self.id, data=payload,
                    ))
                elif event == "cancelled":
                    self.events.publish(Event(
                        type=EventType.NOTICE, session_id=self.id,
                        data={"level": "warn",
                              "text": f"已在 {payload.get('stage', '?')} 处停止本轮"},
                    ))
            except Exception:
                pass

        # 开始新一轮：清掉上次的取消标志
        self._clear_cancel()

        before = snapshot_global()
        error_msg: Optional[str] = None
        try:
            agent_loop(
                self.history, self.perms,
                hooks=self.hooks,
                progress=_progress,
                cancel_check=self.is_cancel_requested,
            )
        except Exception as e:  # pragma: no cover - 运行时保护
            error_msg = str(e)
            self.events.publish(Event(
                type=EventType.ERROR, session_id=self.id,
                data={"message": error_msg},
            ))

        # 把 marker 之后新增的 history 片段 publish 给前端（跳过已经流推过的 tool_use / tool_result）
        self._publish_tail(marker, pushed_tool_use_ids, pushed_tool_result_ids)

        after = snapshot_global()
        self.usage.apply_diff(before, after)
        cancelled = self.is_cancel_requested()
        self.events.publish(Event(
            type=EventType.USAGE, session_id=self.id, data=self.usage.to_dict(),
        ))
        self.events.publish(Event(
            type=EventType.ROUND_END, session_id=self.id,
            data={"error": error_msg, "cancelled": cancelled},
        ))
        self.events.publish(Event(
            type=EventType.PHASE, session_id=self.id,
            data={"state": "idle", "label": ""},
        ))
        if cancelled:
            self.events.publish(Event(
                type=EventType.NOTICE, session_id=self.id,
                data={"level": "ok", "text": "本轮对话已停止。"},
            ))
        # 清理取消标志，避免下次发消息时被误 carry over
        self._clear_cancel()
        self.updated_at = time.time()
        self._set_state("idle")

    def _publish_tail(self, from_index: int,
                      skip_tool_use_ids: set = None,
                      skip_tool_result_ids: set = None) -> None:
        """
        扫 history[from_index:]，按类型把每条 content 拆成事件发出去。

        skip_tool_use_ids:    已在 progress 回调里流式推给前端的 tool_use id 集合；
                              这里遇到同 id 的 tool_use block 就跳过，避免重复渲染。
        skip_tool_result_ids: 已在 progress tool_end 里流式推给前端的 tool_use id 集合；
                              这里遇到同 id 的 tool_result block 就跳过。
        """
        skip_use = skip_tool_use_ids or set()
        skip_res = skip_tool_result_ids or set()
        for msg in self.history[from_index:]:
            role = msg.get("role")
            content = msg.get("content")
            if role == "user" and isinstance(content, list):
                # 这是一条 tool_result-only 的 user 消息（agent_loop 产物）
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_use_id = block.get("tool_use_id")
                        if tool_use_id in skip_res:
                            continue   # 已在 tool_end 时流式推过
                        self.events.publish(Event(
                            type=EventType.TOOL_RESULT, session_id=self.id,
                            data={
                                "tool_use_id": tool_use_id,
                                "content": block.get("content"),
                            },
                        ))
                    elif isinstance(block, dict) and block.get("type") == "text":
                        # 注入的 <scheduled-tasks> 等文本
                        self.events.publish(Event(
                            type=EventType.NOTICE, session_id=self.id,
                            data={"level": "info", "text": block.get("text", "")},
                        ))
            elif role == "user" and isinstance(content, str):
                # agent_loop 内部注入的合并 injection（<scheduled-tasks> 等）
                # 前端已经即时收到 cron_fire 等事件，这里作为可折叠 notice 再呈现一次
                # 由于 marker 位于用户消息之后，这里不会撞到用户输入
                self.events.publish(Event(
                    type=EventType.NOTICE, session_id=self.id,
                    data={"level": "info", "text": content[:800]},
                ))
            elif role == "assistant":
                blocks = serialize_content(content)
                if isinstance(blocks, list):
                    for b in blocks:
                        btype = b.get("type")
                        if btype == "text":
                            self.events.publish(Event(
                                type=EventType.ASSISTANT_TEXT, session_id=self.id,
                                data={"text": b.get("text", "")},
                            ))
                        elif btype == "tool_use":
                            tid = b.get("id")
                            if tid in skip_use:
                                continue   # 已由 progress 流式推过
                            self.events.publish(Event(
                                type=EventType.TOOL_USE, session_id=self.id,
                                data={
                                    "id": tid,
                                    "name": b.get("name"),
                                    "input": b.get("input"),
                                },
                            ))
                elif isinstance(blocks, str):
                    # agent_loop 注入的 "Noted." 桥接消息：刻意静音，避免噪声
                    if blocks != "Noted.":
                        self.events.publish(Event(
                            type=EventType.ASSISTANT_TEXT, session_id=self.id,
                            data={"text": blocks},
                        ))

    # ---------------- 斜杠命令在 worker 里执行（需要操作 history） ----------------

    def _run_compact(self, _arg: str) -> None:
        self._set_state("running")
        if self.history:
            self.history[:] = auto_compact(self.history)
        self.events.publish(Event(
            type=EventType.NOTICE, session_id=self.id,
            data={"level": "info", "text": "已手动压缩历史"},
        ))
        self._set_state("idle")

    def _run_clear(self, hard: str) -> None:
        n = len(self.history)
        self.history[:] = []
        extras: list[str] = []
        if hard == "hard":
            self.usage.reset()
            extras.append("usage")
            if TODO.items:
                TODO.items.clear()
                extras.append("todos")
        suffix = f" (+ {', '.join(extras)})" if extras else ""
        self.events.publish(Event(
            type=EventType.NOTICE, session_id=self.id,
            data={"level": "info", "text": f"已清除 {n} 条消息{suffix}"},
        ))
        self.events.publish(Event(
            type=EventType.SESSION_UPDATED, session_id=self.id,
            data={"cleared": True, "message_count": 0},
        ))
        self.events.publish(Event(
            type=EventType.USAGE, session_id=self.id, data=self.usage.to_dict(),
        ))

    # 提供给 slash_commands 层的入口
    def enqueue_compact(self) -> None:
        self._input_queue.put(("slash_compact", ""))

    def enqueue_clear(self, hard: bool) -> None:
        self._input_queue.put(("slash_clear", "hard" if hard else ""))
