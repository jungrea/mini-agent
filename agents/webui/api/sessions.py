"""
sessions.py —— 会话与消息的 REST 路由。

路由：
    GET    /api/sessions              列表
    POST   /api/sessions               新建   {title?, mode?}
    GET    /api/sessions/{sid}         详情（含 history）
    PATCH  /api/sessions/{sid}         改 title/mode
    DELETE /api/sessions/{sid}         删除
    POST   /api/sessions/{sid}/messages 投递用户消息 {text}
    POST   /api/sessions/{sid}/slash   执行斜杠命令 {line}
    GET    /api/sessions/{sid}/usage   取最新 usage
    GET    /api/slash/commands         前端补全所需的命令列表
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import SLASH_COMMANDS
from ..session_manager import get_manager
from ..slash_commands import run_slash


router = APIRouter(prefix="/api")


class CreateSessionReq(BaseModel):
    title: str = ""
    mode: str = "default"


class PatchSessionReq(BaseModel):
    title: Optional[str] = None
    mode: Optional[str] = None


class MessageReq(BaseModel):
    text: str = Field(..., min_length=1)


class SlashReq(BaseModel):
    line: str = Field(..., min_length=1)


@router.get("/sessions")
def list_sessions():
    return {"sessions": get_manager().list_meta()}


@router.post("/sessions")
def create_session(req: CreateSessionReq):
    sess = get_manager().create(title=req.title, mode=req.mode)
    return sess.to_meta()


@router.get("/sessions/{sid}")
def get_session(sid: str):
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    return sess.to_dict_full()


@router.patch("/sessions/{sid}")
def patch_session(sid: str, req: PatchSessionReq):
    mgr = get_manager()
    sess = mgr.get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    if req.title is not None:
        mgr.rename(sid, req.title)
    if req.mode is not None:
        sess.set_mode(req.mode)
    mgr.persist(sid)
    return sess.to_meta()


@router.delete("/sessions/{sid}")
def delete_session(sid: str):
    ok = get_manager().delete(sid)
    return {"ok": ok}


@router.post("/sessions/{sid}/messages")
def post_message(sid: str, req: MessageReq):
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    # 斜杠命令也从这里转发一次，避免前端判断
    if req.text.strip().startswith("/"):
        result = run_slash(sess, req.text)
        # 让持久化保持同步
        get_manager().persist(sid)
        return {"kind": "slash", "result": result}
    sess.submit_user_message(req.text)
    return {"kind": "accepted", "state": sess.state()}


@router.post("/sessions/{sid}/slash")
def post_slash(sid: str, req: SlashReq):
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    result = run_slash(sess, req.line)
    get_manager().persist(sid)
    return result


@router.get("/sessions/{sid}/usage")
def session_usage(sid: str):
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    return sess.usage.to_dict()


@router.get("/slash/commands")
def slash_commands():
    return {"commands": [{"name": c, "usage": u} for c, u in SLASH_COMMANDS]}
