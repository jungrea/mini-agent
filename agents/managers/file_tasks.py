"""
managers/file_tasks —— 持久化文件任务（可多 agent 抢占）。

对应源 s_full.py 第 353–416 行。

与 TodoManager 的区别：
    * 以 .tasks/task_<id>.json 文件形式落盘，进程重启不丢
    * 支持 blockedBy / blocks 依赖图，以及 owner / status 流转
    * 配合 TeammateManager 的"auto-claim"机制，实现多智能体协作
"""

from __future__ import annotations

import json

from ..core.config import TASKS_DIR


class TaskManager:
    """
    基于文件系统的任务板。

    单个任务的 JSON 结构：
        {
          "id": int,
          "subject": str,
          "description": str,
          "status": "pending"|"in_progress"|"completed"|"deleted",
          "owner": str|None,
          "blockedBy": [int, ...],
          "blocks":    [int, ...]
        }
    """

    def __init__(self):
        TASKS_DIR.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # 私有辅助
    # ------------------------------------------------------------------
    def _next_id(self) -> int:
        """
        下一个任务 ID = 现有 task_*.json 文件里最大 ID + 1。

        这里有意选"文件名里扫 max"而不是维护一个 .counter 文件，
        因为教学脚本追求最少落盘 artefact；并发风险由教学场景接受。
        """
        ids = [int(f.stem.split("_")[1]) for f in TASKS_DIR.glob("task_*.json")]
        return max(ids, default=0) + 1

    def _load(self, tid: int) -> dict:
        """按 ID 读取任务；不存在则抛 ValueError。"""
        p = TASKS_DIR / f"task_{tid}.json"
        if not p.exists():
            raise ValueError(f"Task {tid} not found")
        return json.loads(p.read_text())

    def _save(self, task: dict) -> None:
        """整文件写回（indent=2 方便人类调试 .tasks/ 下的 json）。"""
        (TASKS_DIR / f"task_{task['id']}.json").write_text(json.dumps(task, indent=2))

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------
    def create(self, subject: str, description: str = "") -> str:
        """创建一条新任务，返回其 JSON 字符串（便于 LLM 继续处理）。"""
        task = {
            "id": self._next_id(),
            "subject": subject,
            "description": description,
            "status": "pending",
            "owner": None,
            "blockedBy": [],
            "blocks": [],
        }
        self._save(task)
        return json.dumps(task, indent=2)

    def get(self, tid: int) -> str:
        """获取单个任务的 JSON 文本。"""
        return json.dumps(self._load(tid), indent=2)

    def update(
        self,
        tid: int,
        status: str | None = None,
        add_blocked_by: list | None = None,
        add_blocks: list | None = None,
    ) -> str:
        """
        修改任务的状态或依赖关系。

        关键副作用：
            * status="completed" 时，自动把"所有 blockedBy 包含 tid 的任务"
              中的 tid 从 blockedBy 列表里剔除——实现级联解除阻塞
            * status="deleted" 时，直接物理删除 JSON 文件，立即返回
        """
        task = self._load(tid)

        if status:
            task["status"] = status

            # 级联解除：本任务完成 → 所有"被我阻塞"的任务少一个 blockedBy
            if status == "completed":
                for f in TASKS_DIR.glob("task_*.json"):
                    t = json.loads(f.read_text())
                    if tid in t.get("blockedBy", []):
                        t["blockedBy"].remove(tid)
                        self._save(t)

            # 删除走单独分支：直接 unlink 并立即返回
            if status == "deleted":
                (TASKS_DIR / f"task_{tid}.json").unlink(missing_ok=True)
                return f"Task {tid} deleted"

        if add_blocked_by:
            # set 去重避免重复写入相同依赖
            task["blockedBy"] = list(set(task["blockedBy"] + add_blocked_by))
        if add_blocks:
            task["blocks"] = list(set(task["blocks"] + add_blocks))

        self._save(task)
        return json.dumps(task, indent=2)

    def list_all(self) -> str:
        """人类可读的全量任务列表（按文件名字典序 ≈ 按 ID 升序）。"""
        tasks = [json.loads(f.read_text()) for f in sorted(TASKS_DIR.glob("task_*.json"))]
        if not tasks:
            return "No tasks."

        lines = []
        for t in tasks:
            m = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(
                t["status"], "[?]"
            )
            owner = f" @{t['owner']}" if t.get("owner") else ""
            blocked = f" (blocked by: {t['blockedBy']})" if t.get("blockedBy") else ""
            lines.append(f"{m} #{t['id']}: {t['subject']}{owner}{blocked}")
        return "\n".join(lines)

    def claim(self, tid: int, owner: str) -> str:
        """
        把任务 owner 字段设为 owner，并切到 in_progress。

        被 TeammateManager._loop 的 auto-claim 逻辑频繁调用。
        """
        task = self._load(tid)
        task["owner"] = owner
        task["status"] = "in_progress"
        self._save(task)
        return f"Claimed task #{tid} for {owner}"
