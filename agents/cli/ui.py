"""
cli/ui —— 终端交互 UI 工具。

只放"与业务无关的终端能力"：
    * 读单个按键 / 方向键识别
    * 箭头选择菜单
    * ANSI 常量

这样 permissions/manager 需要弹交互菜单时，可以直接 import 本模块而不必
依赖 cli/repl（避免底层 → 上层的反向依赖）。
"""

from __future__ import annotations

import sys


# ANSI 控制序列
ANSI_RESET: str = "\033[0m"
ANSI_INVERSE: str = "\033[7m"
ANSI_CLEAR_LINE: str = "\033[K"
ANSI_HIDE_CURSOR: str = "\033[?25l"
ANSI_SHOW_CURSOR: str = "\033[?25h"


# ---------------------------------------------------------------------------
# 读单个按键
# ---------------------------------------------------------------------------

def read_key() -> str:
    """
    读取一次键盘事件，返回规范化名称：
        "up" | "down" | "enter" | "ctrl-c" | <单字符小写> | ""

    非 TTY 环境：抛 RuntimeError，由调用方回退。
    """
    if not sys.stdin.isatty():
        raise RuntimeError("stdin is not a TTY")
    if sys.platform == "win32":
        return _read_key_windows()
    return _read_key_posix()


def _read_key_windows() -> str:
    """Windows：msvcrt.getwch() 读取，扩展键（方向键）需两次读取。"""
    import msvcrt  # type: ignore[import-not-found]

    ch = msvcrt.getwch()
    # 扩展键前缀：H=上 / P=下 / K=左 / M=右
    if ch in ("\x00", "\xe0"):
        ch2 = msvcrt.getwch()
        return {"H": "up", "P": "down"}.get(ch2, "")
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x03":
        return "ctrl-c"
    return ch.lower()


def _read_key_posix() -> str:
    """POSIX：原始模式读取，ESC 前缀触发多字节转义序列解析。"""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # ESC [ A/B/C/D —— 方向键
            seq = sys.stdin.read(2)
            return {"[A": "up", "[B": "down"}.get(seq, "")
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            return "ctrl-c"
        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ---------------------------------------------------------------------------
# 箭头菜单
# ---------------------------------------------------------------------------

def arrow_menu(
    options: tuple[str, ...] | list[str],
    default_index: int,
    title: str,
    descriptions: dict[str, str] | None = None,
) -> str:
    """
    极简终端箭头菜单：↑/↓ 切换、回车确认、q/Ctrl-C 取消（取消返回默认项）。
    首字母快捷跳转也可用（如按 `a` 跳到以 a 开头的选项）。

    参数：
        options:       候选项（顺序即展示顺序）
        default_index: 初始高亮索引，也是取消时返回的兜底项
        title:         菜单标题（一行）
        descriptions:  可选，{option: 一行描述}，用作菜单副标题

    返回：被选中的选项字符串。

    依赖 stdin 为 TTY；非 TTY 会在 read_key 内抛 RuntimeError。
    """
    descriptions = descriptions or {}
    index = default_index
    n = len(options)

    def render(first: bool) -> None:
        if not first:
            # 回到菜单起点：标题 1 行 + 选项 n 行 = n+1 行
            sys.stdout.write(f"\033[{n + 1}F")
        sys.stdout.write(ANSI_CLEAR_LINE + title + "\n")
        for i, opt in enumerate(options):
            desc = descriptions.get(opt, "")
            if i == index:
                line = f"{ANSI_INVERSE}▶ {opt:<10}  {desc}{ANSI_RESET}"
            else:
                line = f"  {opt:<10}  {desc}"
            sys.stdout.write(ANSI_CLEAR_LINE + line + "\n")
        sys.stdout.flush()

    sys.stdout.write(ANSI_HIDE_CURSOR)
    try:
        render(first=True)
        while True:
            key = read_key()
            if key == "up":
                index = (index - 1) % n
            elif key == "down":
                index = (index + 1) % n
            elif key == "enter":
                return options[index]
            elif key in ("q", "ctrl-c"):
                return options[default_index]
            else:
                # 首字母快速跳转
                for i, opt in enumerate(options):
                    if opt.startswith(key):
                        index = i
                        break
                else:
                    continue
            render(first=False)
    finally:
        sys.stdout.write(ANSI_SHOW_CURSOR)
        sys.stdout.flush()
