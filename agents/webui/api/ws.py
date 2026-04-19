"""
ws.py —— WebSocket，用于前端对 permission_ask 的回推。

协议（JSON 消息）：
    client → server: {"type": "permission_resolve", "ask_id": "...", "decision": "allow|deny|always"}
    server → client: （当前不主动推，主要通知走 SSE；保留 ping/pong）

为什么用 WS 而非 POST？
    POST 也可以完成 resolve；用 WS 是为了后续扩展（比如前端需要主动取消
    某次运行、或与服务端做双向心跳），且避免在弹窗被频繁点击时发起一堆
    HTTP 连接。当前实现即使用 POST 回推也能工作——所以同时也提供了
    /api/sessions/{sid}/resolve 兜底（见 sessions.py）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..session_manager import get_manager


router = APIRouter(prefix="/api")


@router.websocket("/ws/{sid}")
async def websocket_endpoint(websocket: WebSocket, sid: str):
    sess = get_manager().get(sid)
    if sess is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"error": "invalid json"}))
                continue

            mtype = msg.get("type")
            if mtype == "permission_resolve":
                ask_id = msg.get("ask_id", "")
                decision = msg.get("decision", "deny")
                ok = sess.resolve_permission_ask(ask_id, decision)
                await websocket.send_text(json.dumps({
                    "type": "permission_resolve_ack",
                    "ask_id": ask_id,
                    "ok": ok,
                }))
            elif mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            else:
                await websocket.send_text(json.dumps({"error": f"unknown type: {mtype}"}))
    except WebSocketDisconnect:
        return
