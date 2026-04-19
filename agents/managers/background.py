"""
managers/background —— s08 后台任务。

对应源 s_full.py 第 419–452 行。

提供 fire-and-forget 模式的命令执行：把 shell 命令扔到独立线程里跑，
主循环每轮通过 drain() 非阻塞拉取已完成任务的通知消息。
"""

from __future__ import annotations

import subprocess
import threading
import uuid
from queue import Queue

from ..core.config import WORKDIR


class BackgroundManager:
    """
    用 threading + Queue 实现的极简后台任务管理器。

    状态存储：
        self.tasks[tid] = {"status": "running|completed|error",
                           "command": str, "result": str|None}
        self.notifications: Queue —— 任务结束时投递一条 dict 进来
    """

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.notifications: Queue = Queue()

    def run(self, command: str, timeout: int = 120) -> str:
        """
        启动一个后台任务，立即返回 "Background task <id> started: ..."。

        task_id 是 uuid4 前 8 位，对用户友好。
        """
        tid = str(uuid.uuid4())[:8]
        self.tasks[tid] = {"status": "running", "command": command, "result": None}
        # daemon=True：主进程退出时后台线程不阻塞进程终结
        threading.Thread(target=self._exec, args=(tid, command, timeout), daemon=True).start()
        return f"Background task {tid} started: {command[:80]}"

    def _exec(self, tid: str, command: str, timeout: int) -> None:
        """真正执行命令的 worker（跑在后台线程里）。"""
        try:
            r = subprocess.run(
                command,
                shell=True,
                cwd=WORKDIR,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            # 输出上限 50000 字符，防止长 stream 把内存吃满
            output = (r.stdout + r.stderr).strip()[:50000]
            self.tasks[tid].update({"status": "completed", "result": output or "(no output)"})
        except Exception as e:
            self.tasks[tid].update({"status": "error", "result": str(e)})

        # 结束时往 Queue 里丢一条"迷你通知"（只带 500 字预览），
        # 主循环 drain 时会把它塞进 LLM 上下文
        self.notifications.put({
            "task_id": tid,
            "status": self.tasks[tid]["status"],
            "result": self.tasks[tid]["result"][:500],
        })

    def check(self, tid: str | None = None) -> str:
        """
        查询单个任务状态（tid 指定）或列出所有任务概要（tid=None）。
        """
        if tid:
            t = self.tasks.get(tid)
            return f"[{t['status']}] {t.get('result', '(running)')}" if t else f"Unknown: {tid}"
        return (
            "\n".join(f"{k}: [{v['status']}] {v['command'][:60]}" for k, v in self.tasks.items())
            or "No bg tasks."
        )

    def drain(self) -> list:
        """
        非阻塞地取出队列里所有"已完成通知"。

        主循环 agent_loop 每轮开始会调用一次：有通知则把它们合并成
        <background-results>...</background-results> 段注入 LLM 上下文。
        """
        notifs = []
        while not self.notifications.empty():
            # get_nowait 保证不会阻塞：队列在 empty 返回 False 后仍有可能瞬间空掉，
            # 所以仍然可能抛 queue.Empty；这里教学版不处理极端竞态
            notifs.append(self.notifications.get_nowait())
        return notifs
