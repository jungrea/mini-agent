"""
webui/server —— FastAPI app 装配。

启动时：
  * 初始化 HookManager（与 CLI 一致，支持 .hooks.json）
  * 初始化 SessionManager
  * 安装 cron_bridge 监听 CRON 事件
  * CRON.start() 启动后台调度线程

关闭时：
  * SessionManager.shutdown() 持久化 + 停 worker
  * CRON.stop()
"""

from __future__ import annotations

import atexit
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..core.hooks import HookManager
from ..core.runtime import CRON

from . import cron_bridge
from .api import api_router
from .session_manager import init_manager


logger = logging.getLogger("webui")


def create_app() -> FastAPI:
    app = FastAPI(title="mini-agent WebUI", version="1.0")

    # --- 应用状态初始化 ---
    hooks = HookManager()
    logger.info(hooks.status_line())
    session_result = hooks.run_hooks("SessionStart", {})
    for msg in session_result.get("messages", []):
        logger.info(f"[hook]: {msg}")

    init_manager(hooks=hooks)

    missed = CRON.start()
    if missed:
        logger.info(f"[cron] detected {len(missed)} missed task(s)")
        for m in missed:
            CRON.queue.put(
                f"[Missed scheduled task {m['id']} at {m['missed_at']}]: {m['prompt']}"
            )

    cron_bridge.install()

    atexit.register(_shutdown)

    # --- 路由挂载 ---
    app.include_router(api_router)

    # 静态前端
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(static_dir / "index.html"))

    @app.get("/favicon.ico")
    def favicon():
        return FileResponse(str(static_dir / "favicon.ico")) \
            if (static_dir / "favicon.ico").exists() \
            else FileResponse(str(static_dir / "index.html"))

    @app.get("/api/health")
    def health():
        return {"ok": True}

    return app


def _shutdown() -> None:
    try:
        from .session_manager import MANAGER
        if MANAGER is not None:
            MANAGER.shutdown()
    except Exception:
        pass
    try:
        cron_bridge.uninstall()
        CRON.stop()
    except Exception:
        pass


app = create_app()
