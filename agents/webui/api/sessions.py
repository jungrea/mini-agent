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
    # 会话级工作区（绝对路径 / 可带 ~）；留空 = 用启动时的项目根。
    # 非法路径（不存在 / 非目录 / 系统敏感目录）由 Session 校验后抛 ValueError，
    # 这里捕获后转 400 返回前端。
    workdir: Optional[str] = None


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
    try:
        sess = get_manager().create(
            title=req.title, mode=req.mode, workdir=req.workdir,
        )
    except ValueError as e:
        # workdir 校验失败：不存在 / 非目录 / 系统敏感目录
        raise HTTPException(400, f"workdir 无效: {e}") from e
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


@router.post("/sessions/{sid}/cancel")
def cancel_session(sid: str):
    """
    请求停止该会话当前正在运行的一轮对话。

    返回：
        {"accepted": bool, "state": "idle"|"running", "reason"?: str}

    说明：
        * accepted=True 仅表示"已登记取消请求"，不代表 agent_loop 立即返回；
          Anthropic SDK 的请求一旦发出就无法中途打断。
        * 最终由前端通过 SSE 收到 round_end（cancelled=true）+ status=idle 确认。
    """
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    return sess.request_cancel()


class PermissionResolveReq(BaseModel):
    ask_id: str
    decision: str   # allow / deny / always


@router.post("/sessions/{sid}/permission/resolve")
def resolve_permission(sid: str, req: PermissionResolveReq):
    """
    前端回推权限弹窗决策的 REST 兜底路径（与 WebSocket 同效，幂等）。

    设计原因：WebSocket 在某些场景下可能没握手成功（会话刚建、网络波动等），
    用 REST 双发可以保证 worker 一定能被唤醒，不会超时按 deny 处理。
    """
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    ok = sess.resolve_permission_ask(req.ask_id, req.decision)
    return {"ok": ok, "ask_id": req.ask_id, "decision": req.decision}


@router.get("/sessions/{sid}/pending-asks")
def list_pending_asks(sid: str):
    """调试用：列出该会话当前 pending 的权限 ask。"""
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    with sess._asks_lock:  # noqa: SLF001
        return {"pending": list(sess._pending_asks.keys())}  # noqa: SLF001


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
