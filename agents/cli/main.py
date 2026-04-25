"""
cli/main —— argparse 入口。

子命令：
    repl   —— 默认；启动交互式 REPL（可通过 --mode 指定权限模式）

设计说明：
    本项目对外只提供两种入口——命令行 REPL（本模块）与浏览器 WebUI
    （agents.webui）。一次性、脚本化的单轮命令（如早期的 `run` / `tasks`
    / `team` / `inbox` / `rules`）已被移除，以避免维护两套交互语义；相应
    能力在 REPL 内通过斜杠命令（`/tasks`、`/team` 等）提供。
"""

from __future__ import annotations

import argparse
import sys

from ..permissions.manager import MODES


def _cmd_repl(args: argparse.Namespace) -> int:
    """启动 REPL。延迟 import repl.run_repl，避免无谓的初始化。"""
    from .repl import run_repl
    run_repl(mode=args.mode)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。"""
    parser = argparse.ArgumentParser(
        prog="agents",
        description="Mini Claude Code —— 命令行 REPL 入口（WebUI 见 `python -m agents.webui`）。",
    )
    subparsers = parser.add_subparsers(dest="cmd")

    # repl（唯一子命令，也是默认）
    p_repl = subparsers.add_parser("repl", help="启动交互式 REPL（默认）")
    p_repl.add_argument("--mode", choices=MODES, default=None,
                        help="启动时的权限模式；不指定则在启动时交互式选择")
    p_repl.set_defaults(func=_cmd_repl)

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
