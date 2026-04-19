"""
webui/events —— 每会话一个事件总线 + SSE 事件类型。

设计：worker 线程往 queue.Queue 里投事件；SSE 端用 asyncio.to_thread
从 queue 里拉事件，桥接到 StreamingResponse。一条事件可以 fan-out
给多个订阅者（多浏览器标签），用 list[Queue] 保存订阅者的队列。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from queue import Empty, Queue
from typing import Any


# ---------------------- 事件类型枚举 ----------------------

class EventType:
    """所有 SSE 事件类型；固定字符串常量，避免拼写散落。"""

    # 会话生命周期
    SESSION_CREATED = "session_created"
    SESSION_UPDATED = "session_updated"
    SESSION_DELETED = "session_deleted"

    # 对话流
    USER_MESSAGE    = "user_message"
    ASSISTANT_TEXT  = "assistant_text"
    TOOL_USE        = "tool_use"
    TOOL_RESULT     = "tool_result"
    ROUND_END       = "round_end"
    ERROR           = "error"

    # 细粒度阶段（来自 agent_loop 的 progress 回调）
    PHASE           = "phase"              # 顶层阶段：thinking / tool_running / idle
    LLM_START       = "llm_start"
    LLM_END         = "llm_end"
    TOOL_START      = "tool_start"
    TOOL_END        = "tool_end"
    TOOL_DENIED     = "tool_denied"

    # 用量
    USAGE           = "usage"

    # 权限
    PERMISSION_ASK      = "permission_ask"
    PERMISSION_RESOLVED = "permission_resolved"

    # 定时任务（来自 CronScheduler 监听器）
    CRON_FIRE           = "cron_fire"
    CRON_AUTO_RUN_START = "cron_auto_run_start"
    CRON_AUTO_RUN_DONE  = "cron_auto_run_done"
    CRON_AUTO_RUN_ERROR = "cron_auto_run_error"

    # 状态 / 通知
    STATUS          = "status"         # idle / running
    NOTICE          = "notice"         # 通知栏 toast
    HEARTBEAT       = "heartbeat"


@dataclass
class Event:
    """统一事件结构。to_sse 序列化为 SSE data 行。"""
    type: str
    session_id: str = ""
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        """
        SSE 格式：`data: <json>\n\n`
        使用单行 JSON，浏览器端 EventSource.onmessage 的 event.data 直接 JSON.parse。
        """
        payload = asdict(self)
        return f"data: {json.dumps(payload, ensure_ascii=False, default=_json_default)}\n\n"


def _json_default(o: Any) -> Any:
    """容错序列化：SDK 的 ContentBlock / 其他奇怪对象都退化成 str。"""
    try:
        return dict(o)
    except Exception:
        return str(o)


# ---------------------- 事件总线 ----------------------

class EventBus:
    """
    一个会话一个 EventBus：
        * publish(event)  —— worker 线程 / cron listener / permission bridge 投事件
        * subscribe()     —— SSE 端订阅，返回一个独立队列；unsubscribe 配对使用
    订阅者多时采用 fan-out（每个订阅者拿到同一份事件的副本引用——事件是不可变 dataclass）
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._subscribers: list[Queue] = []

    def subscribe(self) -> Queue:
        q: Queue = Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def publish(self, event: Event) -> None:
        """广播事件到所有订阅者。订阅者队列无大小上限——本地使用场景不会爆。"""
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            q.put(event)

    def drain(self, q: Queue, timeout: float = 0.5) -> list[Event]:
        """
        从单个订阅者队列非阻塞/短阻塞拉事件。SSE 端用它把事件攒成一批。
        首个事件阻塞拉；拿到后再非阻塞把队列里剩余事件一起拉出来。
        """
        out: list[Event] = []
        try:
            first = q.get(timeout=timeout)
            out.append(first)
        except Empty:
            return out
        while True:
            try:
                out.append(q.get_nowait())
            except Empty:
                break
        return out


# ---------------------- 全局事件总线（通知 / cron） ----------------------

#: 面向"未绑定会话"的订阅者（通知栏、cron 面板）使用的全局 bus。
#: cron_bridge 把 cron 事件同时发给 global_bus 和每个活跃会话的 bus，
#: 前端即使还没新建会话也能看到 cron fire。
GLOBAL_BUS: EventBus = EventBus()
