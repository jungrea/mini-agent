"""
webui/cron_bridge —— 把 CronScheduler 的 fire / auto_run 事件接到 EventBus。

注册一次，全生命周期监听。cron 事件同时推 GLOBAL_BUS（通知栏）
和所有活跃会话的 EventBus（让各自的 UI 也能看到）。
"""

from __future__ import annotations

from ..core.runtime import CRON

from .events import Event, EventType, GLOBAL_BUS
from .session_manager import get_manager


_EVENT_TYPE_MAP = {
    "fire":            EventType.CRON_FIRE,
    "auto_run_start":  EventType.CRON_AUTO_RUN_START,
    "auto_run_done":   EventType.CRON_AUTO_RUN_DONE,
    "auto_run_error":  EventType.CRON_AUTO_RUN_ERROR,
}


def _listener(cron_event: dict) -> None:
    etype = _EVENT_TYPE_MAP.get(cron_event.get("type", ""), "")
    if not etype:
        return
    ev = Event(type=etype, session_id="", data=dict(cron_event))
    GLOBAL_BUS.publish(ev)
    # fan-out 到所有活跃会话（让各自的通知栏都能看到）
    try:
        mgr = get_manager()
    except RuntimeError:
        return
    for meta in mgr.list_meta():
        sess = mgr.get(meta["id"])
        if sess is not None:
            sess_ev = Event(type=etype, session_id=sess.id, data=dict(cron_event))
            sess.events.publish(sess_ev)


_installed: bool = False


def install() -> None:
    """把监听器挂到全局 CRON 上。幂等。"""
    global _installed
    if _installed:
        return
    CRON.add_event_listener(_listener)
    _installed = True


def uninstall() -> None:
    global _installed
    if not _installed:
        return
    CRON.remove_event_listener(_listener)
    _installed = False
