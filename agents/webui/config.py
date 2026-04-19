"""
webui/config —— WebUI 独有的常量。

只放 webui 自己的东西；agents 全局常量（WORKDIR / TOKEN_THRESHOLD 等）
仍从 agents.core.config 读取，避免二次定义。
"""

from __future__ import annotations

from pathlib import Path

from ..core.config import WORKDIR


# ------------------------------ 服务端 ------------------------------

#: 默认监听地址。仅绑本地，避免把 shell 权限暴露到局域网。
DEFAULT_HOST: str = "127.0.0.1"

#: 默认端口。选个不太常见的，避免撞 jupyter / vite / 各种 dev server。
DEFAULT_PORT: int = 8765

#: SSE 心跳间隔（秒）。部分代理/浏览器会在 ~30s 无字节时断连，发心跳保活。
SSE_HEARTBEAT_INTERVAL: float = 15.0

#: Worker 线程等待 input_queue 时的 tick（用于优雅 shutdown 响应）
WORKER_POLL_INTERVAL: float = 0.5

#: 权限 ask 弹窗的前端响应超时（秒）。超时视为 deny，避免 worker 永挂。
PERMISSION_ASK_TIMEOUT: float = 180.0


# ------------------------------ 持久化 ------------------------------

#: webui 会话（history + meta）持久化目录
SESSIONS_DIR: Path = WORKDIR / ".claude" / "webui_sessions"

#: 单会话历史保留的最大消息条数（超出触发压缩或截断由 agent_loop 侧处理；
#: 这里仅作为磁盘上安全上限，避免某个会话的 JSON 爆掉）
SESSION_HISTORY_SOFT_LIMIT: int = 4000


# ------------------------------ 前端 -------------------------------

#: 斜杠命令在前端的静态定义（按菜单顺序）
#: 每项：(命令, 用法说明)
#: 维护注意：需要与 webui/slash_commands.py 的 handler 表保持一致
SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/compact",  "手动触发 auto_compact"),
    ("/clear",    "清对话历史；/clear hard 额外清 usage / todos"),
    ("/mode",     "切换权限模式：/mode <default|plan|auto>"),
    ("/usage",    "查看 token 用量；/usage reset 清零"),
    ("/cron",     "定时任务：/cron [list|clear [hard]|test [prompt]]"),
    ("/tasks",    "列出文件任务"),
    ("/team",     "列出 teammate"),
    ("/inbox",    "读取 lead 收件箱"),
    ("/rules",    "展示当前权限规则"),
    ("/prompt",   "打印当前 system prompt"),
    ("/sections", "打印 system prompt 段落目录"),
]
