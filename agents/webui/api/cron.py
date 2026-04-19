"""
cron.py —— 定时任务的 REST 路由。

都走 agents.core.runtime.CRON 全局单例。
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from ...core.runtime import CRON
from ...managers.scheduler import _describe_cron


router = APIRouter(prefix="/api/cron")


def _task_view(t: dict) -> dict:
    now = time.time()
    last = t.get("last_fired")
    return {
        "id": t["id"],
        "cron": t["cron"],
        "cron_human": _describe_cron(t["cron"]),
        "prompt": t["prompt"],
        "prompt_preview": (t["prompt"][:80] + ("…" if len(t["prompt"]) > 80 else "")),
        "recurring": bool(t.get("recurring")),
        "durable": bool(t.get("durable")),
        "auto_run": bool(t.get("auto_run")),
        "created_at": t.get("createdAt"),
        "last_fired": last,
        "last_fired_ago_sec": (now - last) if last else None,
    }


@router.get("")
def list_cron():
    with CRON._lock:
        snapshot = list(CRON.tasks)
    return {"tasks": [_task_view(t) for t in snapshot]}


@router.post("/test")
def cron_fire_test(body: Optional[dict] = Body(default=None)):
    prompt = (body or {}).get("prompt") or "this is a test notification"
    CRON.fire_test(prompt)
    return {"ok": True, "prompt": prompt}


@router.delete("/{task_id}")
def cron_delete(task_id: str):
    msg = CRON.delete(task_id)
    if "not found" in msg:
        raise HTTPException(404, msg)
    return {"ok": True, "message": msg}


class ClearReq(BaseModel):
    hard: bool = False


@router.post("/clear")
def cron_clear(req: ClearReq):
    return {"ok": True, "message": CRON.clear(include_durable=req.hard)}
