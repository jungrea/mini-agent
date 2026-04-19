"""
cli/main —— argparse 入口。

子命令：
    repl   —— 默认；启动交互式 REPL（可通过 --mode 指定权限模式）
    run    —— 单轮非交互：-p <prompt>，跑到 stop_reason != tool_use 退出
    tasks  —— 列出文件任务
    team   —— 列出 teammate
    inbox  —— 读取 lead 收件箱
    rules  —— 展示权限规则（可配 --mode 看不同模式的默认规则集）
"""

from __future__ import annotations

import argparse
import json
import sys

from ..permissions.manager import MODES


def _cmd_repl(args: argparse.Namespace) -> int:
    """启动 REPL。延迟 import repl.run_repl，避免无谓的初始化。"""
    from .repl import run_repl
    run_repl(mode=args.mode)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """
    一次性执行 prompt（相当于只进入 agent_loop 一轮）。

    流程：
        1) 按 --mode 构建 PermissionManager
        2) 把 -p 的内容作为首条 user 消息
        3) 交给 agent_loop 跑到非 tool_use 终止
        4) 打印最后一轮 assistant 的 text 块
    """
    from ..core.loop import agent_loop
    from ..core.runtime import TEAM, build_perms

    perms = build_perms(args.mode)
    TEAM.perms = perms

    messages: list = [{"role": "user", "content": args.prompt}]
    agent_loop(messages, perms)

    # 打印最后一条 assistant 响应里的 text 块
    for msg in reversed(messages):
        if msg["role"] == "assistant":
            content = msg["content"]
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "text"):
                        print(block.text)
            else:
                print(content)
            break
    return 0


def _cmd_tasks(_args: argparse.Namespace) -> int:
    """列出 .tasks/ 下所有任务。"""
    from ..core.runtime import TASK_MGR
    print(TASK_MGR.list_all())
    return 0


def _cmd_team(_args: argparse.Namespace) -> int:
    """列出所有 teammate 及其状态。"""
    from ..core.runtime import TEAM
    print(TEAM.list_all())
    return 0


def _cmd_inbox(_args: argparse.Namespace) -> int:
    """读取并清空 lead 收件箱，以 JSON 输出。"""
    from ..core.runtime import BUS
    print(json.dumps(BUS.read_inbox("lead"), indent=2))
    return 0


def _cmd_rules(args: argparse.Namespace) -> int:
    """
    按 --mode 构造一个 PermissionManager，打印其 rules。

    用于调试/教学：快速查看不同模式下的默认规则集。
    """
    from ..core.runtime import build_perms
    perms = build_perms(args.mode)
    print(f"[Mode: {perms.mode}]")
    for i, rule in enumerate(perms.rules):
        print(f"  {i}: {rule}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。"""
    parser = argparse.ArgumentParser(
        prog="agents",
        description="Mini Claude Code —— s_full.py 的工业化重构版。",
    )
    subparsers = parser.add_subparsers(dest="cmd")

    # repl（默认）
    p_repl = subparsers.add_parser("repl", help="启动交互式 REPL（默认）")
    p_repl.add_argument("--mode", choices=MODES, default=None,
                        help="启动时的权限模式；不指定则在启动时交互式选择")
    p_repl.set_defaults(func=_cmd_repl)

    # run
    p_run = subparsers.add_parser("run", help="单轮非交互执行一个 prompt")
    p_run.add_argument("-p", "--prompt", required=True, help="用户 prompt")
    p_run.add_argument("--mode", choices=MODES, default="default",
                       help="权限模式（默认 default）")
    p_run.set_defaults(func=_cmd_run)

    # tasks
    p_tasks = subparsers.add_parser("tasks", help="列出所有文件任务")
    p_tasks.set_defaults(func=_cmd_tasks)

    # team
    p_team = subparsers.add_parser("team", help="列出所有 teammate 及其状态")
    p_team.set_defaults(func=_cmd_team)

    # inbox
    p_inbox = subparsers.add_parser("inbox", help="读取并清空 lead 收件箱")
    p_inbox.set_defaults(func=_cmd_inbox)

    # rules
    p_rules = subparsers.add_parser("rules", help="展示指定模式下的权限规则")
    p_rules.add_argument("--mode", choices=MODES, default="default",
                         help="要查看的权限模式（默认 default）")
    p_rules.set_defaults(func=_cmd_rules)

    return parser


def main(argv: list[str] | None = None) -> int:
    """
    CLI 主入口。

    行为：
        * 无子命令 → 默认 repl（交互式选择 mode）
        * 有子命令 → 分派到对应 _cmd_* 函数
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # 没传子命令时等价于 `repl`（不指定 --mode，由 repl 内部交互选择）
    if args.cmd is None:
        args.mode = None
        return _cmd_repl(args)

    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
