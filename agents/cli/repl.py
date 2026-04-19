"""
cli/repl —— 交互式 REPL。

对应源 s_full.py 第 805–831 行（基础 REPL 命令），
并参照 s07_permission_system.py 第 423–465 行加入 /mode 与 /rules；
参照 s08_hook_system.py 加入 /hooks 与 SessionStart 触发；
参照 s10_system_prompt.py 加入 /prompt 与 /sections，便于观察 system 装配；
参照 s14_cron_scheduler.py 加入 /cron 与后台调度线程的启停。

REPL 斜杠命令：
    /compact                      —— 手动触发 auto_compact
    /clear [hard]                 —— 清对话历史；hard 同时清 token 用量与内存 todos
    /tasks                        —— 列出文件任务
    /team                         —— 列出 teammate
    /inbox                        —— 读取 lead 收件箱
    /mode <default|plan|auto>     —— 切换权限模式（运行时）
    /rules                        —— 展示当前所有权限规则
    /prompt                       —— 打印当前 system prompt（含 DYNAMIC_BOUNDARY）
    /sections                     —— 打印 system prompt 的段落目录与字符数
    /usage [reset]                —— 打印 token 用量 HUD；reset 清零计数
    /hooks [on|off|reload|list]   —— 外部 hook 系统的启停与查看（空参等价 list）
    /cron [list|clear [hard]|test [prompt]] —— 定时任务查看 / 清空 / 手动触发

提示：输入单个 "/" 后回车，会弹出箭头菜单列出以上所有命令。

扩展：Hook 系统（s08）
    在工作区根目录放置 `.hooks.json` 可注册外部脚本，在以下锚点被调起：
        * SessionStart    —— REPL 启动时执行一次
        * PreToolUse      —— 每次工具调用前（可 block / 改写输入 / 附加 log）
        * PostToolUse     —— 每次工具调用后（可审计 / lint / 附加信息）
    配合退出码契约（0 continue / 1 block / 2 inject）与环境变量
    HOOK_EVENT / HOOK_TOOL_NAME / HOOK_TOOL_INPUT / HOOK_TOOL_OUTPUT，
    可用任意语言的小脚本扩展 agent 行为而无需改源码。可拷贝仓库里的
    `.hooks.json.example` 作为起点。放置 `.hooks.disabled` 空文件即关闭。

扩展：定时任务（s14）
    REPL 启动后会起一个 daemon 后台线程，每分钟检查所有已登记的 cron 任务；
    到点将 prompt 投入通知队列，由 agent_loop 在下一轮开头注入为
    <scheduled-tasks> user 消息。LLM 届时再决定调什么工具——cron 本身
    **不**绕过任何权限。durable 任务落盘 `.claude/scheduled_tasks.json`，
    跨会话保留；recurring 任务 7 天自动过期。详见 managers/scheduler.py。

退出：q / exit / Ctrl-C / Ctrl-D（空行不再退出，只打个新提示符）
"""

from __future__ import annotations

import atexit
import json
import os
from typing import Any, Callable

from ..core.hooks import HookManager
from ..core.loop import agent_loop
from ..core.prompts import BUILDER
from ..core.runtime import BUS, CRON, TASK_MGR, TEAM, TODO, build_perms
from ..core.usage import USAGE, format_hud
from ..managers.compression import auto_compact
from ..permissions.manager import PermissionManager, MODES
from .slash_menu import pick_slash_command
from .ui import arrow_menu


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 跨会话历史持久化位置
_HIST_FILE: str = os.path.expanduser("~/.mini_agent_history")
_HIST_MAX_LINES: int = 1000

# 每种模式的一行中文说明，用于菜单副标题
_MODE_DESCRIPTIONS: dict[str, str] = {
    "default": "高危自动拒绝，其它询问（推荐）",
    "plan":    "只规划不动手：写/执行类工具一律拒绝",
    "auto":    "只读工具秒过，其它仍会询问",
}

# ANSI 控制序列（仅提示符/文本输出相关，键盘与菜单的常量在 ui.py 里）
_ANSI_CYAN: str = "\033[36m"
_ANSI_GREEN: str = "\033[32m"
_ANSI_RESET: str = "\033[0m"

# REPL 输入提示符。
#
# 关于 \001 / \002：
#   GNU readline 约定这两个控制字符（SOH/STX）包裹"不可见字符"（如 ANSI
#   颜色转义），告诉 readline "这段不算可见宽度"。否则 readline 会把
#   "\033[36m" 当 5 个字符算进提示符宽度，导致重画 / 光标定位错位 —— 特别
#   是后台线程异步 print + readline.redisplay() 组合时，这个误差会表现为
#   "提示符消失"或"光标跳到奇怪位置"。
#
# Python 的 input() 内部会原样把 prompt 传给 readline，所以这里直接包好。
#
# _PROMPT_VISIBLE：给 safe_output 用的"纯文本版"（不含 \001\002），因为
#   异步 print 之后我们要自己 write 一次提示符，而 write 到 stdout 时
#   \001\002 反而会当作字面字符显示成 "?"。write 只要 ANSI 转义就够了。
_PROMPT_TEXT: str = "mini-agent >> "
_PROMPT_FOR_INPUT: str = f"\001{_ANSI_CYAN}\002{_PROMPT_TEXT}\001{_ANSI_RESET}\002"
_PROMPT_VISIBLE: str = f"{_ANSI_CYAN}{_PROMPT_TEXT}{_ANSI_RESET}"


# ---------------------------------------------------------------------------
# readline 初始化：让 input() 支持 ← → ↑ ↓、历史、行编辑
# ---------------------------------------------------------------------------

def _setup_readline() -> None:
    """
    启用 readline：input() 自动获得行编辑与历史功能。

    行为：
        * 加载磁盘历史（文件不存在不报错）
        * 设置最大历史长度
        * 注册 atexit 钩子，退出时写回磁盘

    readline 不可用（Windows 无该模块、被裁剪的 Python）时静默跳过，
    不影响主流程——用户只是失去历史/行编辑能力。
    """
    try:
        import readline
    except ImportError:
        return

    try:
        readline.read_history_file(_HIST_FILE)
    except (FileNotFoundError, OSError):
        pass

    readline.set_history_length(_HIST_MAX_LINES)

    def _write_history_on_exit() -> None:
        try:
            readline.write_history_file(_HIST_FILE)
        except OSError:
            # 只读 HOME 等场景：不阻塞退出
            pass

    atexit.register(_write_history_on_exit)


# 模块导入时立即完成 readline 装配——这是唯一合理的时机，因为
# input() 的行为在模块首次 import 后就要改变。
_setup_readline()


# ---------------------------------------------------------------------------
# 启动时选择权限模式
# ---------------------------------------------------------------------------

def _choose_mode_interactive() -> str:
    """
    启动时让用户选择权限模式。

    先尝试箭头菜单；失败（非 TTY / 不支持原始模式）回退到文本输入。
    文本输入下回车 = default，输入非法 = default（容错策略）。
    """
    try:
        return arrow_menu(MODES, default_index=0,
                          title="请选择权限模式（↑/↓ 移动，回车确认）",
                          descriptions=_MODE_DESCRIPTIONS)
    except (RuntimeError, OSError):
        pass  # 退化到文本输入

    print(f"Permission modes: {', '.join(MODES)}")
    try:
        mode_input = input("Mode (default): ").strip().lower() or "default"
    except (EOFError, KeyboardInterrupt):
        return "default"
    if mode_input not in MODES:
        print(f"[Unknown mode '{mode_input}', falling back to 'default']")
        mode_input = "default"
    return mode_input


# ---------------------------------------------------------------------------
# 斜杠命令分派
# ---------------------------------------------------------------------------

def _cmd_compact(_args: str, history: list[Any], _perms: PermissionManager) -> None:
    if history:
        print("[manual compact via /compact]")
        history[:] = auto_compact(history)


def _cmd_tasks(_args: str, _history: list[Any], _perms: PermissionManager) -> None:
    print(TASK_MGR.list_all())


def _cmd_team(_args: str, _history: list[Any], _perms: PermissionManager) -> None:
    print(TEAM.list_all())


def _cmd_inbox(_args: str, _history: list[Any], _perms: PermissionManager) -> None:
    print(json.dumps(BUS.read_inbox("lead"), indent=2))


def _cmd_mode(args: str, _history: list[Any], perms: PermissionManager) -> None:
    target = args.strip()
    if target in MODES:
        perms.mode = target
        print(f"[Switched to {target} mode]")
    else:
        print(f"Usage: /mode <{'|'.join(MODES)}>")


def _cmd_rules(_args: str, _history: list[Any], perms: PermissionManager) -> None:
    for i, rule in enumerate(perms.rules):
        print(f"  {i}: {rule}")


def _cmd_prompt(_args: str, _history: list[Any], perms: PermissionManager) -> None:
    """
    打印当前 system prompt 全文（含 DYNAMIC_BOUNDARY 分隔）。

    按"当前权限模式"装配 dynamic 段，让输出与真实发送给 LLM 的 system
    严格一致——这样教学/调试时看到什么，模型那一轮收到的就是什么。
    """
    text = BUILDER.build(mode=perms.mode)
    print(text)


def _cmd_sections(_args: str, _history: list[Any], perms: PermissionManager) -> None:
    """
    打印 system prompt 的段落目录：每行一个段，标注是否启用与字符数。

    典型输出：
        1. Core          [on ]  420 chars
        2. Tools         [on ] 1820 chars
        3. Skills        [on ]   76 chars
        4. Memory        [off]    0 chars
        5. CLAUDE.md     [off]    0 chars
        6. Dynamic       [on ]  180 chars
    """
    rows = BUILDER.list_sections(mode=perms.mode)
    width = max(len(name) for name, _, _ in rows)
    for name, enabled, size in rows:
        flag = "on " if enabled else "off"
        print(f"  {name.ljust(width)}  [{flag}]  {size:5d} chars")


def _cmd_usage(args: str, _history: list[Any], _perms: PermissionManager) -> None:
    """
    打印一次 token 用量 HUD；`/usage reset` 会清零计数。

    REPL 每轮结束本来就自动打 HUD；这个命令主要用于：
        * 对话静默时手动查看
        * 调试 / 教学演示（清零后重新观察）
    """
    if args.strip() == "reset":
        USAGE.reset()
        print("[usage counters reset]")
        return
    print(format_hud())


def _cmd_cron(args: str, _history: list[Any], _perms: PermissionManager) -> None:
    """
    定时任务相关子命令（s14 融合）。

    用法：
        /cron                  等价 /cron list
        /cron list             列出所有 session + durable 任务
        /cron clear            只清 session 任务（保留 durable）
        /cron clear hard       连 durable 一起清（含 .claude/scheduled_tasks.json）
        /cron test [prompt]    手动往通知队列投一条，下一轮 agent_loop 会注入给 LLM
                               用于教学演示"到点触发"路径，无需真等 cron 时间

    注：cron_create / cron_delete 是给 LLM 用的工具（见 dispatch.py），
    用户端一般不手动新建（真要的话，通过与 LLM 对话触发 cron_create 即可）。
    """
    stripped = args.strip()

    if stripped == "" or stripped.lower() == "list":
        print(CRON.list_tasks())
        return

    if stripped.lower().startswith("clear"):
        rest = stripped[5:].strip().lower()
        include_durable = (rest == "hard")
        print(CRON.clear(include_durable=include_durable))
        return

    if stripped.lower().startswith("test"):
        # /cron test 或 /cron test 自定义提示内容
        prompt = stripped[4:].strip() or "this is a test notification"
        CRON.fire_test(prompt)
        print(f"[cron] test notification enqueued: {prompt!r}")
        print("[cron] will be injected at the start of the next agent_loop iteration")
        return

    print("Usage: /cron [list | clear [hard] | test [prompt]]")


def _cmd_clear(args: str, history: list[Any], _perms: PermissionManager) -> None:
    """
    清除当前会话的对话历史。

    用法：
        /clear          —— 只清对话历史（LLM 的"记忆"归零）。轻量、最常用。
        /clear hard     —— 对话历史 + token 用量计数器 + 内存 todos 全部清零。
                           相当于"这个 REPL 会话从零开始"。

    为什么这个功能有用？
        * 会话被之前的主题污染（比如从"天气"切到"代码"时，LLM 还会夹带上下文）
        * HUD 爆红 / auto_compact 前主动断开重来，比让它 LLM 压缩更干净
        * 教学演示时想"重来一次"而不退出进程

    不影响的东西（刻意保留）：
        * `.tasks/` 文件任务、`.team/` teammate 配置、`.transcripts/` 历史备份
          —— 这些是跨会话持久化资源；`/clear` 只清本次会话的内存态。
        * 权限模式（perms.mode）—— 想切换走 `/mode`
        * skills / CLAUDE.md / .memory —— 它们每轮都会重新装配进 system prompt

    实现细节：
        * 用 `history[:] = []` 就地清空，保持调用方持有的引用不失效
        * hard 模式下调 `USAGE.reset()` 和 `TODO.items.clear()`
    """
    flag = args.strip().lower()
    if flag not in ("", "hard"):
        print("Usage: /clear [hard]")
        return

    # 1) 对话历史：就地清空（引用保留，REPL 主循环的 history 变量继续可用）
    n = len(history)
    history[:] = []

    # 2) hard 模式：额外清 token 用量计数 + 内存 todos
    extras: list[str] = []
    if flag == "hard":
        USAGE.reset()
        extras.append("usage counters")
        if TODO.items:
            TODO.items.clear()
            extras.append("todos")

    suffix = f" (+ {', '.join(extras)})" if extras else ""
    print(f"[cleared {n} messages{suffix}]")


# 斜杠命令分派表：命令名 → handler(args_tail, history, perms)
#
# 注：`/hooks` 不在这里——它需要访问 HookManager 实例，采用闭包方式
# 在 run_repl 内部动态注入到 dispatch 里，以避免把 hooks 变成全局状态。
_SLASH_COMMANDS: dict[str, Callable[[str, list[Any], PermissionManager], None]] = {
    "/compact":  _cmd_compact,
    "/clear":    _cmd_clear,
    "/tasks":    _cmd_tasks,
    "/team":     _cmd_team,
    "/inbox":    _cmd_inbox,
    "/mode":     _cmd_mode,
    "/rules":    _cmd_rules,
    "/prompt":   _cmd_prompt,
    "/sections": _cmd_sections,
    "/usage":    _cmd_usage,
    "/cron":     _cmd_cron,
}


# 展示在 "/" 菜单里的用法描述（仅供 UI 展示，不参与分派）。
# 与 _SLASH_COMMANDS 并列而非合并，是为了让分派表保持"名字→函数"的纯粹性。
_SLASH_USAGES: dict[str, str] = {
    "/compact":  "/compact                        手动触发 auto_compact",
    "/clear":    "/clear [hard]                   清对话历史；hard 额外清用量与 todos",
    "/tasks":    "/tasks                          列出文件任务",
    "/team":     "/team                           列出 teammate",
    "/inbox":    "/inbox                          读取 lead 收件箱",
    "/mode":     "/mode <default|plan|auto>       切换权限模式",
    "/rules":    "/rules                          展示当前所有权限规则",
    "/prompt":   "/prompt                         打印当前 system prompt 全文",
    "/sections": "/sections                       打印 system prompt 段落目录",
    "/usage":    "/usage [reset]                  打印 token 用量；reset 清零",
    "/hooks":    "/hooks [on|off|reload|list]     外部 hook 系统启停与查看",
    "/cron":     "/cron [list|clear [hard]|test]  定时任务查看 / 清空 / 手动触发",
}


def _try_handle_slash(query: str, history: list[Any], perms: PermissionManager) -> bool:
    """
    若 query 是斜杠命令则执行并返回 True；否则返回 False 让主循环继续。
    拆分 "/mode auto" → ("/mode", "auto")，未知命令视为非斜杠（交给 LLM）。
    """
    stripped = query.strip()
    if not stripped.startswith("/"):
        return False
    head, _, tail = stripped.partition(" ")
    handler = _SLASH_COMMANDS.get(head)
    if handler is None:
        return False
    handler(tail, history, perms)
    return True


# ---------------------------------------------------------------------------
# 输出辅助
# ---------------------------------------------------------------------------

def _print_last_assistant(history: list[Any]) -> None:
    """
    打印 history 末尾那条 assistant 消息中的 text 块。

    agent_loop 自身只打印工具调用，不打印最终的 assistant 文本，
    所以 REPL 在每轮结束后调用本函数补足用户可见的回答。
    """
    for msg in reversed(history):
        if msg["role"] != "assistant":
            continue
        content = msg["content"]
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    print(f"{_ANSI_GREEN}{text}{_ANSI_RESET}")
        else:
            print(f"{_ANSI_GREEN}{content}{_ANSI_RESET}")
        return


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def run_repl(mode: str | None = None) -> None:
    """
    启动 REPL。

    参数：
        mode: 若提供（例如 CLI 传入 --mode auto），跳过交互式选择；
              否则启动时提示用户选择。

    对话历史保存在本地变量 history 中（进程退出即丢）。
    跨会话保留的是 **输入行历史**（由 readline 落盘到 ~/.mini_agent_history），
    而不是对话本身；如需对话备份参见 auto_compact 的 .transcripts/ 目录。
    """
    # --- 异步输出：告诉 safe_print 我们的提示符长什么样 ---
    # 后台线程（CronScheduler / 未来的其它异步源）通过 safe_print 打印时，
    # 会在消息后面把这个提示符重新 write 回去，避免"fire 后下一行空白"。
    # 用 try / except 保护：safe_output 只在 cli 包内使用，理论上一定可
    # import；但保守起见不让"提示符注册"这种装饰性功能阻塞 REPL 启动。
    try:
        from .safe_output import register_prompt
        register_prompt(_PROMPT_VISIBLE)
    except ImportError:
        pass

    # --- 权限初始化 ---
    if mode is None:
        mode = _choose_mode_interactive()
    perms = build_perms(mode)
    # 让 teammate 循环复用同一个权限管理器（teammate 下 ask 会自动退化为 deny）
    TEAM.perms = perms
    print(f"[Permission mode: {mode}]")

    # --- Cron 调度器初始化（s14 融合）---
    # start() 做三件事：拿跨进程锁、载入 durable 任务、启动后台线程。
    # 返回的 missed 是启动时回看 24h 内"会话关闭期间错过的"durable 任务，
    # 注入为 <scheduled-tasks> 让 LLM 决定补不补（不强制执行）。
    missed = CRON.start()
    if missed:
        print(f"[cron] detected {len(missed)} missed task(s) while session was closed")
        # 漏触发交给 LLM 处理：把每条 missed 作为首轮待注入信息保留。
        # 这里不直接 append 到 history（history 在下面初始化），而是先把
        # 它们塞到队列里——让它们走正常的 drain_notifications 路径，
        # 确保与"真正到点触发"的处理路径一致。
        for m in missed:
            CRON.queue.put(
                f"[Missed scheduled task {m['id']} at {m['missed_at']}]: {m['prompt']}"
            )
    # 注册退出钩子，保证 REPL Ctrl-D / exit 时释放锁、停后台线程。
    # 放这里而不是 main.py：因为 CRON.start() 在这里才调用，配对更直观。
    atexit.register(CRON.stop)

    # --- Hook 系统初始化（s08 融合）---
    # 从当前工作区加载 .hooks.json；文件不存在则 HookManager 空转，对
    # 现有流程零影响。加载成功会打一行 status，让用户知道扩展层是否生效。
    hooks = HookManager()
    print(hooks.status_line())

    # SessionStart：REPL 启动时触发一次，典型用途——打印 git 状态 / 预热缓存
    # / 加载偏好设置。失败不阻止 REPL 启动（HookManager 内部已吞掉异常）。
    session_result = hooks.run_hooks("SessionStart", {})
    for msg in session_result["messages"]:
        print(f"[hook]: {msg}")
    if session_result["blocked"]:
        # SessionStart 被 block 并不致命；仅打印原因供观察，REPL 继续启动
        print(f"[hook]: SessionStart reported block ({session_result['block_reason']}); continuing")

    # --- /hooks 斜杠命令（闭包：捕获 hooks 实例而不污染全局 dispatch）---
    def _cmd_hooks(args: str, _history: list[Any], _perms: PermissionManager) -> None:
        sub = args.strip().lower()
        if sub == "" or sub == "list":
            rows = hooks.list_hooks()
            print(hooks.status_line())
            if not rows:
                return
            for row in rows:
                cmd = row["command"]
                if len(cmd) > 60:
                    cmd = cmd[:57] + "…"
                print(f"  {row['event']:<14} matcher={row['matcher']!s:<12} {cmd}")
            return
        if sub == "on":
            hooks.enable()
            print(hooks.status_line())
            return
        if sub == "off":
            hooks.disable()
            print(hooks.status_line())
            return
        if sub == "reload":
            n = hooks.reload()
            # reload 不自动 enable：如果之前被 /hooks off，保持 off 状态
            print(f"[hooks] reloaded {n} entr{'y' if n == 1 else 'ies'} from {hooks.config_name}")
            print(hooks.status_line())
            return
        print("Usage: /hooks [on|off|reload|list]")

    # 把闭包注入到分派表（仅本次 run_repl 作用域有效，退出自然消失）。
    # 这是刻意的设计：避免把 hooks 变成模块级全局，保持 HookManager
    # 的生命周期与 REPL 进程严格对应。
    _SLASH_COMMANDS["/hooks"] = _cmd_hooks

    # --- REPL 主循环 ---
    history: list[Any] = []

    def _run_chosen_slash(chosen: str) -> None:
        """执行菜单选中的命令：带参只提示用法，无参立即执行。"""
        usage = _SLASH_USAGES[chosen].strip()
        if "<" in usage:  # 用法里出现 "<...>" 视为带参
            print(f"Usage: {usage}")
            return
        _try_handle_slash(chosen, history, perms)

    while True:
        try:
            query = input(_PROMPT_FOR_INPUT)
        except (EOFError, KeyboardInterrupt):
            break

        stripped = query.strip()

        # 空行：什么都不做，继续下一轮提示（不再把"回车"视为退出）
        if stripped == "":
            continue

        # 显式退出关键字
        if stripped.lower() in ("q", "exit"):
            break

        # 单个 "/" ：弹出斜杠命令菜单让用户挑一个
        if stripped == "/":
            chosen = pick_slash_command(_SLASH_USAGES)
            if chosen is None:
                continue
            _run_chosen_slash(chosen)
            continue

        # 斜杠命令分支：不进入 agent_loop
        if _try_handle_slash(query, history, perms):
            continue

        # 普通对话：作为 user 消息驱动一轮 agent_loop
        history.append({"role": "user", "content": query})
        agent_loop(history, perms, hooks=hooks)
        _print_last_assistant(history)
        # 每轮回答结束打一次 HUD：让用户能看到"这轮消耗了多少 token"。
        # 注意：不在下一轮提示符前重复刷（避免屏幕上出现两条完全相同的 HUD）。
        print(format_hud())
        print()

    # REPL 退出：把注入的 /hooks 从 dispatch 移除，避免下次同进程内再起
    # REPL 时引用旧 hooks 实例。正常一进程只跑一次 REPL，这里只是保险。
    _SLASH_COMMANDS.pop("/hooks", None)

    # 注销异步打印用的提示符，避免同进程后续非 REPL 场景里（极少见）
    # safe_print 还试图重画一个已经不相关的 prompt。
    try:
        from .safe_output import register_prompt
        register_prompt("")
    except ImportError:
        pass
