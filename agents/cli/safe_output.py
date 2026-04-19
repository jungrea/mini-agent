"""
cli/safe_output —— 后台线程安全地向终端打印，不撞乱 input() 提示符。

场景：
    REPL 主线程阻塞在 input() 时，后台线程（CronScheduler 每分钟检查、
    HookManager 子进程等）直接调 print 会把内容接在提示符同一行，视觉
    错乱。UNIX 工具（zsh/bash job-control、less status line、tmux）
    的经典解法是：打印前清掉当前行，打印后"重画"被中断的输入行。

为什么不只用 readline.redisplay()：
    readline.redisplay() 仅在"当前有活动输入缓冲"时才真正重画。如果用户
    刚进提示符还没敲字符，缓冲为空，它什么也不做——导致我们清完行之后，
    屏幕上就没有提示符了，下一行变成空的，直到用户敲下一个键才会触发
    readline 再画一次。

所以本模块同时做两件事：
    1. 打印消息前 `\r\033[2K` 清整行
    2. 打印消息后由调用方（REPL）事先 register 的 prompt 提示符被重新
       write 回 stdout；如果此时 readline 有未提交的输入缓冲，再调
       readline.redisplay() 把那段也追画出来

另外关于 prompt 里的 ANSI 颜色：
    GNU readline 要求"不可见字符"用 \\001 / \\002（SOH/STX）包起来才能
    正确计算提示符宽度。register 在这里的 prompt 应当是"可见文本版"
    （含 ANSI 但不含 \\001\\002），因为我们是用 stdout.write() 打印，
    write 不认识 \\001\\002 —— 会把它们字面显示成 "?"。
"""

from __future__ import annotations

import sys
import threading
from typing import Any, TextIO


# 单进程共享一把锁：所有异步 print 串行化，避免"一条消息被另一条腰斩"
_PRINT_LOCK: threading.Lock = threading.Lock()

# ANSI 清行序列：
#   \r       —— 光标回到行首
#   \033[2K  —— 清整行（CSI 2K，不含光标移动，与 \r 配合才是完整清行）
_CLEAR_LINE: str = "\r\033[2K"

# 当前已注册的提示符（REPL 启动时注册，退出时清空）。
# 未注册时 = ""，safe_print 就只清行不重画 —— 等价于老行为，避免在
# 非 REPL 场景（CLI 单轮、teammate 子进程）里凭空打出一行提示符。
_current_prompt: str = ""


def register_prompt(prompt: str) -> None:
    """
    REPL 主循环启动时告诉本模块"当前的提示符长什么样"。

    约定：传入的 prompt 应当是可直接写到 stdout 的形式（可以含 ANSI 颜色
    转义，但不要含 readline 专用的 \\001/\\002 标记——后者只对 input() /
    readline 有意义，write 到 stdout 会显示成字面 "?"）。

    空字符串 = 取消注册，恢复到"只清行不重画"的保守行为。
    """
    global _current_prompt
    _current_prompt = prompt


def safe_print(*args: Any, sep: str = " ", file: TextIO | None = None) -> None:
    """
    线程安全地打印消息，打印后重画当前提示符 + 已输入内容（若有）。

    参数与 print() 近似（仅保留常用的 sep / file，省略 end）。

    读取规则：
        * sys.stdout 不是 tty → 当作日志场景，去掉 ANSI 清行 / 不重画提示符
        * _current_prompt 为空（未注册 / 已注销）→ 清行 + 打印，不重画
          （避免在非 REPL 进程里凭空冒出一个 prompt）
        * readline 可用 → 调用 redisplay() 让未提交的输入缓冲也追画出来
    """
    stream = file or sys.stdout
    message = sep.join(str(a) for a in args)

    # 非交互式：直接写，不做 ANSI（避免污染日志文件）
    if not stream.isatty():
        with _PRINT_LOCK:
            stream.write(message + "\n")
            stream.flush()
        return

    with _PRINT_LOCK:
        # 1) 清掉当前整行（input() 正在画的提示符 / 用户半截输入）
        stream.write(_CLEAR_LINE)
        # 2) 写消息本身
        stream.write(message + "\n")

        # 3) 重画提示符——注册过才画，避免非 REPL 场景误画
        if _current_prompt:
            stream.write(_current_prompt)

        stream.flush()

        # 4) 若 readline 有未提交的输入缓冲（用户敲了半截还没回车），
        #    让它把这段追加画出来。缓冲为空时本调用基本是 no-op。
        try:
            import readline
            readline.redisplay()
        except ImportError:
            pass
        except Exception:
            # redisplay() 在非标准 tty 下可能抛异常——不能让后台 print 失败
            pass
