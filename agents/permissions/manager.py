"""
permissions/manager —— s07 权限管理器。

参考 learn-claude-code-main/agents/s07_permission_system.py 第 132–281 行。

核心管线（**顺序不可换**）：
    Step 0. bash 校验：命中 severe 直接 deny；命中 escalate 升级为 ask
    Step 1. deny 规则（旁路免疫，最先执行——即使 auto 模式也绕不过）
    Step 2. mode 判定：plan → 写工具一律 deny / 其它 allow；
                       auto → 只读工具 allow / 其它走到下一步；
                       default → 全部走到下一步
    Step 3. allow 规则
    Step 4. ask_user（默认落点）

交互：ask_user 支持 y / n / always 三种回答；always 会把"当前工具 + path=*"
追加成持久 allow 规则，避免同一工具反复弹问。

断路器：consecutive_denials 达到阈值时打印提示，引导用户切 plan 模式。
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from typing import Callable, Optional

from .validators import bash_validator


# webui 等非 TTY 前端可通过 PermissionManager(ask_callback=...) 注入异步交互；
# 回调签名：(tool_name, tool_input) -> "allow" | "deny" | "always"
AskCallback = Callable[[str, dict], str]


# 三种权限模式；顺序不影响行为，只是对外枚举顺序
MODES: tuple[str, ...] = ("default", "plan", "auto")

# 只读工具集（auto 模式下会被直通）
READ_ONLY_TOOLS: frozenset = frozenset({
    "read_file", "bash_readonly",
    "search_content", "web_fetch", "web_search",
    "cron_list",
})

# 会"改变世界"的工具集（plan 模式下一律 deny）
WRITE_TOOLS: frozenset = frozenset({"write_file", "edit_file", "bash"})

# 默认规则：按列表顺序匹配，first-match-wins
# 规则字段：
#   tool:     工具名或 "*"
#   path:     glob 匹配工具入参里的 path（可选）
#   content:  glob 匹配 bash command（可选，仅对 bash 有意义）
#   behavior: "allow" | "deny" | "ask"
DEFAULT_RULES: list[dict] = [
    # === deny：兜底拦死若干高危 bash（放在最前，deny 旁路免疫）===
    {"tool": "bash", "content": "rm -rf /*",   "behavior": "deny"},
    {"tool": "bash", "content": "rm -rf /",    "behavior": "deny"},
    {"tool": "bash", "content": "sudo *",      "behavior": "deny"},
    {"tool": "bash", "content": "mkfs*",       "behavior": "deny"},
    {"tool": "bash", "content": "dd if=*",     "behavior": "deny"},
    {"tool": "bash", "content": ":(){ :|:& };:", "behavior": "deny"},

    # === allow：读 / "无副作用"工具默认放行，避免骚扰 ===
    # 读文件：路径任意
    {"tool": "read_file",        "path": "*",   "behavior": "allow"},
    # 代码 / 网络搜索类：只读、不改磁盘
    {"tool": "search_content",   "behavior": "allow"},
    {"tool": "web_fetch",        "behavior": "allow"},
    {"tool": "web_search",       "behavior": "allow"},
    # 纯查询 / 列清单 / 内存型工具（不写磁盘、不执行外部命令）
    {"tool": "load_skill",       "behavior": "allow"},
    {"tool": "TodoWrite",        "behavior": "allow"},
    {"tool": "compress",         "behavior": "allow"},
    {"tool": "task_list",        "behavior": "allow"},
    {"tool": "task_get",         "behavior": "allow"},
    {"tool": "list_teammates",   "behavior": "allow"},
    {"tool": "read_inbox",       "behavior": "allow"},
    {"tool": "check_background", "behavior": "allow"},
    # s14: cron 查询纯只读；而 cron_create / cron_delete 刻意不在 allow 清单——
    # 落到默认 ask，避免 LLM 在"帮用户排定时任务 / 清空任务"上越权。
    {"tool": "cron_list",        "behavior": "allow"},

    # === allow：常见只读 bash 命令（按命令名前缀放行）===========================
    # 匹配规则：fnmatch 对 command 做 glob。形如 "ls*" 会匹配 "ls"、"ls -la"、"ls /path"。
    # 风险控制：
    #   * Step 0 的 bash validator 仍然在前拦截（反引号/$(/IFS=）
    #   * deny 规则位于 allow 之前，sudo/rm -rf 等仍被拒
    #   * 只允许"典型只读"命令；带副作用的 cp/mv/rm/chmod/chown/git commit 等仍会 ask
    {"tool": "bash", "content": "ls",          "behavior": "allow"},
    {"tool": "bash", "content": "ls *",        "behavior": "allow"},
    {"tool": "bash", "content": "pwd",         "behavior": "allow"},
    {"tool": "bash", "content": "cat *",       "behavior": "allow"},
    {"tool": "bash", "content": "head *",      "behavior": "allow"},
    {"tool": "bash", "content": "tail *",      "behavior": "allow"},
    {"tool": "bash", "content": "wc *",        "behavior": "allow"},
    {"tool": "bash", "content": "grep *",      "behavior": "allow"},
    {"tool": "bash", "content": "rg *",        "behavior": "allow"},  # ripgrep
    {"tool": "bash", "content": "find *",      "behavior": "allow"},
    {"tool": "bash", "content": "which *",     "behavior": "allow"},
    {"tool": "bash", "content": "file *",      "behavior": "allow"},
    {"tool": "bash", "content": "stat *",      "behavior": "allow"},
    {"tool": "bash", "content": "du *",        "behavior": "allow"},
    {"tool": "bash", "content": "df *",        "behavior": "allow"},
    {"tool": "bash", "content": "tree *",      "behavior": "allow"},
    {"tool": "bash", "content": "echo *",      "behavior": "allow"},
    {"tool": "bash", "content": "printf *",    "behavior": "allow"},
    {"tool": "bash", "content": "date*",       "behavior": "allow"},
    {"tool": "bash", "content": "whoami",      "behavior": "allow"},
    {"tool": "bash", "content": "uname*",      "behavior": "allow"},
    {"tool": "bash", "content": "env",         "behavior": "allow"},
    # Git 只读子命令（commit/push/merge/reset 等会改动仓库的仍 ask）
    {"tool": "bash", "content": "git status*", "behavior": "allow"},
    {"tool": "bash", "content": "git log*",    "behavior": "allow"},
    {"tool": "bash", "content": "git diff*",   "behavior": "allow"},
    {"tool": "bash", "content": "git show*",   "behavior": "allow"},
    {"tool": "bash", "content": "git branch*", "behavior": "allow"},
    {"tool": "bash", "content": "git remote*", "behavior": "allow"},
    # 后续所有未匹配的工具 → 走到 Step 4 的 ask（即：会真正跳出提示的那一类）
]


class PermissionManager:
    """
    管理单个会话的权限决策。

    典型用法：
        perms = PermissionManager(mode="default")
        decision = perms.check(tool_name, tool_input)
        if decision["behavior"] == "allow":
            execute(...)
        elif decision["behavior"] == "ask":
            if perms.ask_user(tool_name, tool_input):
                execute(...)
        else:
            # deny —— 把 reason 作为 tool_result 回传 LLM，让它换策略
            ...
    """

    def __init__(self, mode: str = "default", rules: list | None = None,
                 ask_callback: Optional[AskCallback] = None):
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode}. Choose from {MODES}")

        self.mode: str = mode
        # 复制一份默认规则，允许运行时用 /mode 或 always 动态追加而不污染类变量
        self.rules: list[dict] = rules if rules is not None else list(DEFAULT_RULES)

        # 断路器：连续多少次 deny 就打印一次建议
        self.consecutive_denials: int = 0
        self.max_consecutive_denials: int = 3

        # 可选的异步 ask 回调（webui 等前端注入）。为 None 时走原有终端交互。
        # 回调返回字符串 "allow" / "deny" / "always"；任何异常或其他返回值按 deny 处理。
        self.ask_callback: Optional[AskCallback] = ask_callback

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def check(self, tool_name: str, tool_input: dict) -> dict:
        """
        对一次工具调用做决策，返回 {"behavior": "allow"|"deny"|"ask", "reason": str}。

        之所以把 bash 校验放最前面：
            这是"客观层面的安全校验"（基于代码特征而非规则字符串），
            应当在任何 allow 规则之前，否则 auto 模式下可能让 `sudo ls` 漏过。
        """
        # --- Step 0: bash 安全校验 ---------------------------------------
        if tool_name == "bash":
            command = tool_input.get("command", "")
            failures = bash_validator.validate(command)
            if failures:
                # severe 类别：立即 deny，不给用户降级选项
                severe = {"sudo", "rm_rf"}
                severe_hits = [f for f in failures if f[0] in severe]
                if severe_hits:
                    desc = bash_validator.describe_failures(command)
                    return {"behavior": "deny", "reason": f"Bash validator: {desc}"}
                # 非 severe 类别：升级到 ask（用户仍可放行）
                desc = bash_validator.describe_failures(command)
                return {"behavior": "ask", "reason": f"Bash validator flagged: {desc}"}

        # --- Step 1: deny 规则（旁路免疫） ------------------------------
        for rule in self.rules:
            if rule["behavior"] != "deny":
                continue
            if self._matches(rule, tool_name, tool_input):
                return {"behavior": "deny", "reason": f"Blocked by deny rule: {rule}"}

        # --- Step 2: mode 判定 ------------------------------------------
        if self.mode == "plan":
            # plan 模式：写工具一律拒绝；其它都放行（用于"先规划不动手"场景）
            if tool_name in WRITE_TOOLS:
                return {"behavior": "deny", "reason": "Plan mode: write operations are blocked"}
            return {"behavior": "allow", "reason": "Plan mode: read-only allowed"}

        if self.mode == "auto":
            # auto 模式：只读工具秒过；其它掉到 allow 规则 / 询问
            if tool_name in READ_ONLY_TOOLS or tool_name == "read_file":
                return {"behavior": "allow", "reason": "Auto mode: read-only tool auto-approved"}
            # 非只读工具不在这里决策，继续往下走 allow 规则

        # --- Step 3: allow 规则 -----------------------------------------
        for rule in self.rules:
            if rule["behavior"] != "allow":
                continue
            if self._matches(rule, tool_name, tool_input):
                # 命中 allow：重置断路器
                self.consecutive_denials = 0
                return {"behavior": "allow", "reason": f"Matched allow rule: {rule}"}

        # --- Step 4: 默认 ask ------------------------------------------
        return {"behavior": "ask", "reason": f"No rule matched for {tool_name}, asking user"}

    # ------------------------------------------------------------------
    # 交互式询问
    # ------------------------------------------------------------------
    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        """
        询问用户是否允许本次工具调用。

        交互形式：
            * 优先使用 ↑/↓ 箭头菜单（TTY 环境）—— 避免输入单词拼写错误
            * 菜单不可用时回退到 y/n/always 文本输入

        三种行为一致：
            allow  / y / yes → 本次放行
            always           → 本次放行，并把 {"tool": tool_name, "path": "*",
                               "behavior": "allow"} 追加到 self.rules
            deny   / n / 其它 → 拒绝；累计 consecutive_denials，触达阈值给提示
        """
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        # 终端路径下打印；webui 回调路径下这行也会打印到服务端控制台，便于审计
        print(f"\n  [Permission] {tool_name}: {preview}")

        answer = self._prompt_answer(tool_name, tool_input)
        if answer is None:
            # 用户按 Ctrl-C / 非 TTY 输入失败 → 按拒绝处理
            self.consecutive_denials += 1
            return False

        if answer == "always":
            # 动态追加一条持久 allow 规则
            self.rules.append({"tool": tool_name, "path": "*", "behavior": "allow"})
            self.consecutive_denials = 0
            return True
        if answer == "allow":
            self.consecutive_denials = 0
            return True

        # deny 路径：累计、提醒
        self.consecutive_denials += 1
        if self.consecutive_denials >= self.max_consecutive_denials:
            print(f"  [{self.consecutive_denials} consecutive denials -- "
                  "consider switching to plan mode]")
        return False

    def _prompt_answer(self, tool_name: str, tool_input: dict | None = None) -> str | None:
        """
        获取用户选择，返回 "allow" | "always" | "deny"；失败返回 None。

        优先级：
            1) 若注入了 ask_callback（webui 等异步前端），走回调
            2) 否则先尝试箭头菜单（TTY）
            3) 再回退到 y/n/always 文本输入
        """
        # 1) 异步回调优先：webui 通过 WebSocket 弹窗
        if self.ask_callback is not None:
            try:
                answer = self.ask_callback(tool_name, tool_input or {})
            except Exception:
                return None
            if answer in ("allow", "deny", "always"):
                return answer
            return "deny"

        # 箭头菜单分支（延迟 import 避免循环依赖，同时避免 CLI 未安装时报错）
        try:
            from ..cli.ui import arrow_menu
            options = ("allow", "deny", "always")
            descriptions = {
                "allow":  "本次允许（相当于 y）",
                "deny":   "本次拒绝（相当于 n）",
                "always": f"始终允许本工具 {tool_name}（本进程内）",
            }
            return arrow_menu(options, default_index=0,
                              title="  Allow? (↑/↓ 移动，回车确认，q 取消)",
                              descriptions=descriptions)
        except (RuntimeError, OSError):
            pass  # 非 TTY / 不支持原始模式 → 文本输入兜底
        except ImportError:
            pass  # cli.ui 不可用（测试隔离等场景）→ 文本输入兜底

        # 文本输入兜底：保留原有 y/n/always 协议
        try:
            raw = input("  Allow? (y/n/always): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None

        if raw == "always":
            return "always"
        if raw in ("y", "yes"):
            return "allow"
        return "deny"

    # ------------------------------------------------------------------
    # 内部：规则匹配
    # ------------------------------------------------------------------
    def _matches(self, rule: dict, tool_name: str, tool_input: dict) -> bool:
        """
        判断 rule 是否匹配当前工具调用。

        三个可选字段（缺省或 "*" 视为通配）：
            tool:    工具名精确匹配
            path:    对 tool_input["path"] 做 fnmatch glob 匹配
            content: 对 tool_input["command"] 做 fnmatch glob 匹配
        只有全部提供的字段都匹配才算整条规则匹配。
        """
        # 1) 工具名
        if rule.get("tool") and rule["tool"] != "*":
            if rule["tool"] != tool_name:
                return False
        # 2) path glob
        if "path" in rule and rule["path"] != "*":
            path = tool_input.get("path", "")
            if not fnmatch(path, rule["path"]):
                return False
        # 3) command glob（bash 专用）
        if "content" in rule:
            command = tool_input.get("command", "")
            if not fnmatch(command, rule["content"]):
                return False
        return True
