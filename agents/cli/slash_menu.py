"""
cli/slash_menu —— 斜杠命令选择菜单。

职责（刻意做得很窄）：
    * 仅提供"根据一份命令清单，弹出箭头菜单让用户挑一个"的能力
    * 不感知任何具体命令的语义，不持有命令注册表
    * 非 TTY / 菜单异常时优雅降级为"打印清单"，绝不阻塞 REPL

设计取舍（为什么不把 _SLASH_COMMANDS 搬进来）：
    命令的真实分派器是 repl._try_handle_slash，它需要 history / perms 这些
    REPL 态上下文。如果把注册表搬过来，本模块就被迫依赖 PermissionManager
    等上层类型，变成"UI 模块知道业务"。所以这里只接受一个"命令名 → 用法串"
    的纯字符串映射，UI 与业务解耦。

触发时机：REPL 主循环里当整行只有一个 "/" 时调用 `pick_slash_command`。

为什么不做"敲 '/' 就立即弹"？
    尝试过（见 git 历史中的方案 B）：在 input() 之前先用 read_key() 拦第一个
    字符。问题是 read_key() 切 raw 模式 + 恢复的过程，会扰乱 readline 对当前
    行光标起点的记忆，导致之后 input() 里的退格能越过 prompt 删到行首。
    Python 标准库的 readline 没有暴露 rl_bind_key(C 回调) 的能力，想彻底绕过
    这个问题只能引入 prompt_toolkit 这类第三方依赖——不符合本项目"最少依赖、
    教学为主"的定位。因此保留"/ + 回车"的两步触发，零 bug、零额外依赖。
"""

from __future__ import annotations

from .ui import arrow_menu


def pick_slash_command(usages: dict[str, str]) -> str | None:
    """
    弹出箭头菜单让用户从 `usages` 的 key 里挑一个斜杠命令。

    参数：
        usages: 形如 {"/mode": "/mode <default|plan|auto>", "/tasks": "/tasks", ...}
                key 是要展示和返回的命令名；value 是一行用法描述，作为菜单副标题。

    返回：
        被选中的命令名（即 usages 的某个 key），或 None 表示用户取消 / 终端不支持。

    终端不是 TTY 或 arrow_menu 抛 RuntimeError/OSError 时，退化为把命令清单
    打印到屏幕后返回 None——让 REPL 主循环直接进入下一轮提示符。
    """
    if not usages:
        return None

    options = list(usages.keys())
    try:
        chosen = arrow_menu(
            options,
            default_index=0,
            title="斜杠命令（↑/↓ 选择，回车确认，q/Ctrl-C 取消）",
            descriptions=usages,
        )
    except (RuntimeError, OSError):
        # 非 TTY 降级：打印清单
        print("Available slash commands:")
        for _name, usage in usages.items():
            print(f"  {usage}")
        return None

    return chosen
