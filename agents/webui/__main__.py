"""
python -m agents.webui —— WebUI 启动入口。

参数：
    --host       绑定地址（默认 127.0.0.1）
    --port       端口（默认 8765）
    --no-open    启动后不自动打开浏览器
    --log-level  uvicorn 日志级别（默认 info）

注：权限模式不在这里固化；每个会话在创建时指定 mode（前端下拉）。
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import webbrowser

import uvicorn

from .config import DEFAULT_HOST, DEFAULT_PORT


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m agents.webui")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-open", action="store_true",
                        help="不要自动打开浏览器")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    url = f"http://{args.host}:{args.port}"
    print(f"[agents.webui] starting on {url}")

    if not args.no_open:
        # 延迟 1.5s 打开浏览器，让 uvicorn 先监听成功
        def _open():
            time.sleep(1.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        "agents.webui.server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
