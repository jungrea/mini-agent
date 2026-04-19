"""
team/messaging —— s09 消息总线。

对应源 s_full.py 第 455–482 行。

设计：
    每个成员一个收件箱文件 `.team/inbox/<name>.jsonl`，
    发送方用 "a" 模式追加写、接收方用"读后清空"语义消费。
    这种 jsonl 文件队列的好处是：
        * 零依赖（不需要 broker）
        * 跨进程可见（将来 teammate 开到子进程/子机器时依然能用）
        * 断电/崩溃后不会丢消息（至多重复读——但 read_inbox 读完立刻清空，
          所以实际单进程内也不会重复消费）
"""

from __future__ import annotations

import json
import time

from ..core.config import INBOX_DIR


class MessageBus:
    """
    文件系统消息总线。

    消息结构（追加到对方 jsonl 的每一行）：
        {
          "type": "message" | "broadcast" | "shutdown_request"
                  | "shutdown_response" | "plan_approval_response",
          "from": str,
          "content": str,
          "timestamp": float,
          ... 额外字段由 extra 传入（如 request_id）
        }
    """

    def __init__(self):
        INBOX_DIR.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict | None = None,
    ) -> str:
        """
        给名为 to 的成员发一条消息。

        关键实现细节：
            * "a" 追加写：保证并发发送不会互相覆盖
            * timestamp 用 time.time()（float 秒），方便接收方排序
            * extra 允许塞协议字段（如 request_id），与 type 形成协议对
        """
        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            msg.update(extra)

        with open(INBOX_DIR / f"{to}.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")
        return f"Sent {msg_type} to {to}"

    def read_inbox(self, name: str) -> list:
        """
        读取并**清空** name 的收件箱，返回本次读到的所有消息列表。

        "读后清空"是故意的——被读过的消息不再二次消费。
        如果需要审计留痕，应由上层保存到别处（例如塞进 messages 历史）。
        """
        path = INBOX_DIR / f"{name}.jsonl"
        if not path.exists():
            return []

        msgs = [json.loads(l) for l in path.read_text().strip().splitlines() if l]
        path.write_text("")
        return msgs

    def broadcast(self, sender: str, content: str, names: list) -> str:
        """
        给 names 里除了 sender 之外的所有成员发 type="broadcast" 消息。

        不反向发给自己（避免自己收到自己的广播形成循环）。
        """
        count = 0
        for n in names:
            if n != sender:
                self.send(sender, n, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"
