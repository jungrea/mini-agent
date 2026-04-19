"""
permissions/validators —— bash 命令校验 + 工作区信任检查。

参考 learn-claude-code-main/agents/s07_permission_system.py 第 55–129 行。

教学版只包含少量高风险正则；真实系统可以扩展到规则库 / LLM 二次判别 / 沙箱。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.config import WORKDIR


class BashSecurityValidator:
    """
    一组轻量正则校验器，检测 bash 命令中的"明显危险模式"。

    返回的校验失败会由 PermissionManager 进一步决定：
        * severe（sudo / rm_rf）→ 立即 deny
        * 其它（反引号替换 / $(...) / IFS 注入）→ escalate 为 ask（用户可放行）

    关于"哪些 shell 元字符算可疑"的设计取舍
    ------------------------------------------------------------------
    早期版本把 ``;`` / ``&`` / ``|`` 也列入可疑（"shell_metachar"）。
    实践中日常命令大量使用管道/逻辑串联（``find . | grep | head``、
    ``make && ./run``），每次都弹窗骚扰价值 < 安全价值，因此**移除这三个**。

    保留下来的两类更接近真正的"注入入口"：
        * 反引号 `` `...` ``        —— 命令替换旧语法，基本没有日常使用场景
        * ``$(...)``                —— 命令替换新语法，LLM 偶尔滥用
        * ``IFS=``                  —— 字段分隔符篡改，典型注入手段

    纯 ``$VAR`` 变量展开不在拦截范围（``$`` 只有后接 ``(`` 或位于 ``IFS=``
    上下文才被认为危险）。
    """

    # 基础正则，每条对应一个"类别名"——类别名用于前述 severe 判定
    VALIDATORS: list[tuple[str, str]] = [
        ("sudo",             r"\bsudo\b"),                 # 提权
        ("rm_rf",            r"\brm\s+(-[a-zA-Z]*)?r"),    # 递归删除
        ("backtick",         r"`"),                        # 反引号命令替换 `...`
        ("cmd_substitution", r"\$\("),                     # 命令替换 $(...)
        ("ifs_injection",    r"\bIFS\s*="),                # IFS 变量篡改
    ]

    def validate(self, command: str) -> list[tuple[str, str]]:
        """
        用所有校验器检查命令，返回所有命中的 (名称, 正则) 列表。

        空列表表示命令通过了全部校验。
        """
        failures: list[tuple[str, str]] = []
        for name, pattern in self.VALIDATORS:
            if re.search(pattern, command):
                failures.append((name, pattern))
        return failures

    def is_safe(self, command: str) -> bool:
        """便捷方法：所有校验器都未命中时返回 True。"""
        return len(self.validate(command)) == 0

    def describe_failures(self, command: str) -> str:
        """把命中的校验器拼成人类可读的一行诊断。"""
        failures = self.validate(command)
        if not failures:
            return "No issues detected"
        parts = [f"{name} (pattern: {pattern})" for name, pattern in failures]
        return "Security flags: " + ", ".join(parts)


def is_workspace_trusted(workspace: Path | None = None) -> bool:
    """
    判断工作区是否被显式标记为"可信"。

    教学版使用一个简单的标记文件 `<workspace>/.claude/.claude_trusted`。
    产品化时可以扩展为签名校验、团队白名单、CI 标记等。
    """
    ws = workspace or WORKDIR
    trust_marker = ws / ".claude" / ".claude_trusted"
    return trust_marker.exists()


# 模块级单例：整个权限管线共享同一个 validator 实例（验证器无状态）
bash_validator: BashSecurityValidator = BashSecurityValidator()
