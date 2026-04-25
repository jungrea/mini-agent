"""
webui/session_manager —— 会话字典 + 磁盘持久化。

文件布局：
    .claude/webui_sessions/
        <session_id>.json     单会话 meta + history

只有 history 落盘；usage / perms / events bus 是运行时态不持久化。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from ..core.hooks import HookManager
from ..permissions.manager import MODES

from .config import SESSIONS_DIR
from .events import Event, EventType, GLOBAL_BUS
from .session import Session, serialize_message


class SessionManager:
    """进程级单例：维护所有活跃会话 + 持久化。"""

    def __init__(self, hooks: Optional[HookManager] = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock: threading.RLock = threading.RLock()
        self._hooks: Optional[HookManager] = hooks
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

        # 启动时不自动恢复所有会话到内存（避免重启时一次性创建 N 个 worker 线程）。
        # 而是前端"选中某会话"时才 lazy-load。

    # ---------------- CRUD ----------------

    def create(self, title: str = "", mode: str = "default",
               workdir: Optional[str] = None) -> Session:
        """
        新建会话。

        workdir：本会话的"文件 / bash / 搜索"工具沙箱根；None=用全局 WORKDIR。
                 校验由 Session.__init__ 内的 validate_workdir 完成；非法路径
                 会抛 ValueError，由调用方（REST handler）转 400 给前端。
        """
        if mode not in MODES:
            mode = "default"
        sid = uuid.uuid4().hex[:12]
        if not title:
            title = f"新对话 {time.strftime('%H:%M')}"
        sess = Session(id=sid, title=title, mode=mode, history=[],
                       hooks=self._hooks, workdir=workdir)
        with self._lock:
            self._sessions[sid] = sess
        self._persist(sess)
        GLOBAL_BUS.publish(Event(
            type=EventType.SESSION_CREATED, session_id=sid, data=sess.to_meta(),
        ))
        return sess

    def get(self, sid: str) -> Optional[Session]:
        with self._lock:
            sess = self._sessions.get(sid)
            if sess is not None:
                return sess
        # lazy-load：尝试从磁盘恢复
        return self._load(sid)

    def list_meta(self) -> list[dict]:
        """
        列出所有会话元数据：内存 + 磁盘 union，按 updated_at 降序。
        """
        metas: dict[str, dict] = {}
        with self._lock:
            for sid, sess in self._sessions.items():
                metas[sid] = sess.to_meta()
        # 合并磁盘上的
        if SESSIONS_DIR.exists():
            for p in SESSIONS_DIR.glob("*.json"):
                sid = p.stem
                if sid in metas:
                    continue
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                metas[sid] = {
                    "id": sid,
                    "title": raw.get("title", sid[:8]),
                    "mode": raw.get("mode", "default"),
                    "created_at": raw.get("created_at", 0),
                    "updated_at": raw.get("updated_at", 0),
                    "message_count": len(raw.get("history", [])),
                    "state": "idle",
                    # 老存档没有 workdir 字段时返回 None（= 项目根），完全兼容
                    "workdir": raw.get("workdir"),
                }
        out = list(metas.values())
        out.sort(key=lambda m: m.get("updated_at", 0), reverse=True)
        return out

    def rename(self, sid: str, title: str) -> bool:
        sess = self.get(sid)
        if sess is None:
            return False
        sess.title = title[:80].strip() or sess.title
        sess.updated_at = time.time()
        self._persist(sess)
        GLOBAL_BUS.publish(Event(
            type=EventType.SESSION_UPDATED, session_id=sid, data=sess.to_meta(),
        ))
        return True

    def delete(self, sid: str) -> bool:
        with self._lock:
            sess = self._sessions.pop(sid, None)
        if sess is not None:
            sess.stop()
        # 删磁盘
        p = SESSIONS_DIR / f"{sid}.json"
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
        GLOBAL_BUS.publish(Event(
            type=EventType.SESSION_DELETED, session_id=sid, data={},
        ))
        return True

    # ---------------- 持久化 ----------------

    def persist(self, sid: str) -> None:
        """外部（如每轮结束）触发 persist。"""
        sess = self.get(sid)
        if sess is not None:
            self._persist(sess)

    def _persist(self, sess: Session) -> None:
        payload = {
            "id": sess.id,
            "title": sess.title,
            "mode": sess.mode,
            "created_at": sess.created_at,
            "updated_at": sess.updated_at,
            # workdir 写绝对路径字符串；None 表示用全局项目根
            "workdir": str(sess.workdir_path) if sess.workdir_path else None,
            "history": [serialize_message(m) for m in sess.history],
        }
        p = SESSIONS_DIR / f"{sess.id}.json"
        tmp = p.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, p)
        except OSError:
            pass

    def _load(self, sid: str) -> Optional[Session]:
        p = SESSIONS_DIR / f"{sid}.json"
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        sess = Session(
            id=sid,
            title=raw.get("title", sid[:8]),
            mode=raw.get("mode", "default"),
            history=raw.get("history", []),
            hooks=self._hooks,
            workdir=raw.get("workdir"),
        )
        sess.created_at = raw.get("created_at", sess.created_at)
        sess.updated_at = raw.get("updated_at", sess.updated_at)
        with self._lock:
            self._sessions[sid] = sess
        return sess

    # ---------------- 生命周期 ----------------

    def shutdown(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for sess in sessions:
            try:
                self._persist(sess)
            except Exception:
                pass
            sess.stop()


# 进程级单例。由 server.py 启动时实例化、注入。
MANAGER: Optional[SessionManager] = None


def init_manager(hooks: Optional[HookManager] = None) -> SessionManager:
    global MANAGER
    if MANAGER is None:
        MANAGER = SessionManager(hooks=hooks)
    return MANAGER


def get_manager() -> SessionManager:
    if MANAGER is None:
        raise RuntimeError("SessionManager not initialized; call init_manager() first")
    return MANAGER
