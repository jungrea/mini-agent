"""permissions —— s07 权限控制管线。

说明：源 s_full.py 声称包含 s07 但实际代码中缺失，
本子包参考 learn-claude-code-main/agents/s07_permission_system.py 补齐：

    validators: BashSecurityValidator（bash 命令安全校验）+ is_workspace_trusted
    manager:    PermissionManager + MODES + READ_ONLY_TOOLS / WRITE_TOOLS / DEFAULT_RULES
"""
