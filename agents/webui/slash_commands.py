"""
webui/slash_commands —— Web 版斜杠命令 handlers。

与 CLI repl.py 的斜杠命令语义一致，但：
  * /compact 和 /clear 会真正改 history，所以走 session 的 worker queue；
  * /mode 走 session.set_mode；
  * 其它（/usage /cron /tasks /team /inbox /rules /prompt /sections）
    都是"读取并返回文本"，直接同步执行即可。

每个 handler 返回 dict：{"output": str, "kind": "text"}
"""

from __future__ import annotations

import json
from typing import Any

from ..core.prompts import BUILDER
from ..core.runtime import BUS, CRON, TASK_MGR, TEAM
from ..core.usage import USAGE, format_hud
from ..permissions.manager import MODES

from .session import Session


def _result(text: str) -> dict:
    return {"output": text, "kind": "text"}


def cmd_compact(sess: Session, _args: str) -> dict:
    sess.enqueue_compact()
    return _result("已排入手动压缩任务，稍后查看对话。")


def cmd_clear(sess: Session, args: str) -> dict:
    hard = args.strip().lower() == "hard"
    sess.enqueue_clear(hard)
    return _result("已排入清除任务。")


def cmd_mode(sess: Session, args: str) -> dict:
    target = args.strip()
    if target not in MODES:
        return _result(f"Usage: /mode <{'|'.join(MODES)}>")
    return _result(sess.set_mode(target))


def cmd_rules(sess: Session, _args: str) -> dict:
    lines = [f"  {i}: {rule}" for i, rule in enumerate(sess.perms.rules)]
    return _result("\n".join(lines) or "(no rules)")


def cmd_prompt(sess: Session, _args: str) -> dict:
    return _result(BUILDER.build(mode=sess.perms.mode))


def cmd_sections(sess: Session, _args: str) -> dict:
    rows = BUILDER.list_sections(mode=sess.perms.mode)
    width = max((len(name) for name, _, _ in rows), default=0)
    out = []
    for name, enabled, size in rows:
        flag = "on " if enabled else "off"
        out.append(f"  {name.ljust(width)}  [{flag}]  {size:5d} chars")
    return _result("\n".join(out))


def cmd_usage(sess: Session, args: str) -> dict:
    if args.strip() == "reset":
        sess.usage.reset()
        USAGE.reset()
        return _result("[usage counters reset]")
    # 前端 HUD 用 session.usage 渲染；这里返回一行纯文本 hud 便于查看
    return _result(format_hud(color=False))


def cmd_cron(sess: Session, args: str) -> dict:
    stripped = args.strip()
    if stripped == "" or stripped.lower() == "list":
        return _result(CRON.list_tasks())
    if stripped.lower().startswith("clear"):
        rest = stripped[5:].strip().lower()
        include_durable = rest == "hard"
        return _result(CRON.clear(include_durable=include_durable))
    if stripped.lower().startswith("test"):
        prompt = stripped[4:].strip() or "this is a test notification"
        CRON.fire_test(prompt)
        return _result(f"[cron] test notification enqueued: {prompt!r}")
    return _result("Usage: /cron [list | clear [hard] | test [prompt]]")


def cmd_tasks(_sess: Session, _args: str) -> dict:
    return _result(TASK_MGR.list_all())


def cmd_team(_sess: Session, _args: str) -> dict:
    return _result(TEAM.list_all())


def cmd_inbox(_sess: Session, _args: str) -> dict:
    return _result(json.dumps(BUS.read_inbox("lead"), indent=2, ensure_ascii=False))


HANDLERS: dict[str, Any] = {
    "/compact":  cmd_compact,
    "/clear":    cmd_clear,
    "/mode":     cmd_mode,
    "/rules":    cmd_rules,
    "/prompt":   cmd_prompt,
    "/sections": cmd_sections,
    "/usage":    cmd_usage,
    "/cron":     cmd_cron,
    "/tasks":    cmd_tasks,
    "/team":     cmd_team,
    "/inbox":    cmd_inbox,
}


def run_slash(sess: Session, line: str) -> dict:
    """
    line 形如 "/mode auto"；找到 handler 执行。
    未知命令返回 {"output": ..., "kind": "error"}。
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return {"output": "not a slash command", "kind": "error"}
    head, _, tail = stripped.partition(" ")
    handler = HANDLERS.get(head)
    if handler is None:
        return {"output": f"Unknown command: {head}", "kind": "error"}
    try:
        return handler(sess, tail)
    except Exception as e:
        return {"output": f"Error: {e}", "kind": "error"}
