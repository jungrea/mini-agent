"""
stream.py —— SSE 流式事件推送。

路由：
    GET /api/stream/{sid}     订阅指定会话的 EventBus
    GET /api/stream/global    订阅全局事件（cron / 跨会话通知）

实现要点：
    * asyncio.to_thread 在异步上下文里跑 queue.get（阻塞）—— 避免阻塞事件循环
    * 周期性发送心跳事件，防止代理/浏览器 30s 空闲断连
    * 客户端断开时（asyncio.CancelledError）务必 unsubscribe 释放队列
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..config import SSE_HEARTBEAT_INTERVAL
from ..events import Event, EventBus, EventType, GLOBAL_BUS
from ..session_manager import get_manager


router = APIRouter(prefix="/api")


async def _sse_stream(bus: EventBus, request: Request, scope: str):
    """把 EventBus 的阻塞队列桥接成 async 生成器。"""
    q = bus.subscribe()
    try:
        # 首包：立即下发一个 ready 事件，让前端知道通道已建立
        ready = Event(type=EventType.HEARTBEAT, session_id=scope,
                      data={"ready": True, "ts": time.time()})
        yield ready.to_sse()

        while True:
            if await request.is_disconnected():
                break
            # 阻塞式 get 放到线程池，超时后发心跳
            event: Event | None = await asyncio.to_thread(
                _safe_get, q, SSE_HEARTBEAT_INTERVAL,
            )
            if event is None:
                # 心跳
                yield Event(type=EventType.HEARTBEAT, session_id=scope,
                            data={"ts": time.time()}).to_sse()
                continue
            yield event.to_sse()
    finally:
        bus.unsubscribe(q)


def _safe_get(q, timeout: float):
    """queue.Queue.get 带超时，返回 None 表示超时。"""
    from queue import Empty
    try:
        return q.get(timeout=timeout)
    except Empty:
        return None


@router.get("/stream/global")
async def stream_global(request: Request):
    """订阅全局事件（cron fire / 会话创建/删除 / 全局通知）。"""
    return StreamingResponse(
        _sse_stream(GLOBAL_BUS, request, scope="global"),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/stream/{sid}")
async def stream_session(sid: str, request: Request):
    sess = get_manager().get(sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    return StreamingResponse(
        _sse_stream(sess.events, request, scope=sid),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
