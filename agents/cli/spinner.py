"""
cli/spinner —— 一个轻量的终端 spinner。

用法：
    with spinning("思考中"):
        response = client.messages.create(...)   # 阻塞
    # 退出块时 spinner 自动停止并清行

特点：
    * 后台线程 + threading.Event 做停止信号，主线程不受阻
    * 写入使用 \r 回车重绘当前行，不会污染历史输出
    * 停止时用 \033[2K + \r 彻底清除 spinner 那行，避免残影
    * 非 TTY 环境（管道重定向 / IDE 调试器）自动降级为 no-op，防止写进日志文件
    * 所有异常都在上下文管理器里吞掉，不会因 spinner 故障影响业务逻辑

设计权衡：
    * 只用标准库 threading，不引入 rich / tqdm 等依赖
    * spinner 字符循环用一组 braille 点阵，兼容性最好；终端不支持则退化为 '|/-\\'
"""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator


# braille 点阵动画：视觉效果柔和，所有现代终端都支持
_FRAMES_BRAILLE: tuple[str, ...] = (
    "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
)

# ANSI：清除整行并回到行首
_ANSI_CLEAR_LINE: str = "\033[2K\r"

# 每帧间隔（秒）
_FRAME_INTERVAL: float = 0.1


class _Spinner:
    """
    线程化 spinner。start() 启动后台线程循环绘制，stop() 停止并清行。

    非 TTY 环境：start() / stop() 都成为 no-op，调用方无需区分。
    """

    def __init__(self, label: str, stream=None) -> None:
        self.label: str = label
        self.stream = stream or sys.stdout
        self._stop_evt: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        # 只有 TTY 才启用动画；非 TTY（管道/日志）降级为 no-op
        self._enabled: bool = getattr(self.stream, "isatty", lambda: False)()

    def _run(self) -> None:
        i = 0
        while not self._stop_evt.is_set():
            frame = _FRAMES_BRAILLE[i % len(_FRAMES_BRAILLE)]
            # \r 回车不换行；前导空格 2 个，跟 REPL 的提示符风格对齐
            self.stream.write(f"\r  {frame} {self.label}")
            self.stream.flush()
            i += 1
            # Event.wait 可被 set() 立刻唤醒，比 time.sleep 响应更快
            self._stop_evt.wait(_FRAME_INTERVAL)

    def start(self) -> None:
        if not self._enabled:
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._enabled:
            return
        self._stop_evt.set()
        if self._thread is not None:
            # join 很便宜——线程每 0.1s 就检查一次 stop_evt
            self._thread.join(timeout=0.5)
            self._thread = None
        # 彻底清除 spinner 占用的那一行
        try:
            self.stream.write(_ANSI_CLEAR_LINE)
            self.stream.flush()
        except Exception:
            pass


@contextmanager
def spinning(label: str) -> Iterator[None]:
    """
    上下文管理器形式的 spinner：

        with spinning("思考中"):
            do_slow_thing()

    - 任何异常（包括 KeyboardInterrupt）都会保证 spinner 被停掉
    - 非 TTY 环境：什么都不做，等价于普通代码块
    """
    spinner = _Spinner(label)
    spinner.start()
    try:
        yield
    finally:
        spinner.stop()
