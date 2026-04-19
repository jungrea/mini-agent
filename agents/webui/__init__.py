"""
agents.webui —— mini-agent 的浏览器 UI 入口（与 agents.cli 并列）。

设计原则：agents 的核心模块（core/ managers/ permissions/ tools/ team/）
保持为纯库；agents.cli 与 agents.webui 各自是一个"前端层"，按需启用。

启动：
    python -m agents.webui [--host 127.0.0.1] [--port 8765]

与 cli 的共享：所有全局单例（CRON / TODO / BG / TEAM / BUS / USAGE）
都来自 agents.core.runtime，webui 不重新实例化；这意味着同一进程内
不应该同时运行 cli REPL 和 webui（CRON 跨进程锁会限制）。
"""
