"""
managers/todos —— s03 短期待办清单。

对应源 s_full.py 第 183–217 行。

与 file_tasks（持久化任务）的区别：
    * TodoManager 只在内存里维护"本轮会话"的小清单，进程退出即丢
    * 适合 LLM 做"短期任务拆解 + 勾完就忘"
    * 主循环 agent_loop 会基于 has_open_items() 做 nag 提醒
"""


class TodoManager:
    """
    内存版 Todo 列表管理器。

    数据结构：
        self.items = [
            {"content": "...", "status": "pending|in_progress|completed",
             "activeForm": "doing xxx"},
            ...
        ]
    约束：
        * 最多 20 项
        * 同一时刻至多一个 in_progress（模拟"单线程专注"）
    """

    def __init__(self):
        self.items: list[dict] = []

    def update(self, items: list) -> str:
        """
        覆盖式更新整个 todo 列表。

        参数：
            items: list[dict]，每个元素至少包含 content / status / activeForm

        返回：
            调用 render() 得到的文本可视化
        抛出：
            ValueError —— 任一字段缺失、状态非法、in_progress 超过 1 个、总数超 20
        """
        validated: list[dict] = []
        ip = 0  # in_progress 数量

        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            af = str(item.get("activeForm", "")).strip()

            if not content:
                raise ValueError(f"Item {i}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            if not af:
                raise ValueError(f"Item {i}: activeForm required")

            if status == "in_progress":
                ip += 1

            validated.append({"content": content, "status": status, "activeForm": af})

        if len(validated) > 20:
            raise ValueError("Max 20 todos")
        # 多于 1 个 in_progress 会让 nag 和人类阅读混乱
        if ip > 1:
            raise ValueError("Only one in_progress allowed")

        self.items = validated
        return self.render()

    def render(self) -> str:
        """
        把当前 todos 渲染成人眼友好的符号化文本。

        状态符号映射：completed [x] / in_progress [>] / pending [ ] / 未知 [?]
        in_progress 条目在末尾追加 " <- {activeForm}" 暗示"正在做的事的现在进行时"
        """
        if not self.items:
            return "No todos."

        lines = []
        for item in self.items:
            m = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(
                item["status"], "[?]"
            )
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            lines.append(f"{m} {item['content']}{suffix}")

        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

    def has_open_items(self) -> bool:
        """是否仍有未完成项（用于 agent_loop 的 nag 计数器）。"""
        return any(item.get("status") != "completed" for item in self.items)
