"""webui/api —— REST / SSE / WebSocket 路由聚合。"""

from fastapi import APIRouter

from .sessions import router as sessions_router
from .stream import router as stream_router
from .ws import router as ws_router
from .cron import router as cron_router


api_router = APIRouter()
api_router.include_router(sessions_router)
api_router.include_router(stream_router)
api_router.include_router(ws_router)
api_router.include_router(cron_router)
