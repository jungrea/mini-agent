"""
agents —— 迷你版 Claude Code 骨架。

本包把 `learn-claude-code-main/agents/s_full.py`（s01–s18 的完整本地机制）
按工业级项目风格拆分到以下子包：

    core/         编排层：config、prompts、dispatch、loop、runtime
    tools/        无状态外部交互：persisted_output、fs、bash、subagent
    managers/     进程内有状态能力：todos、skills、compression、file_tasks、background
    permissions/  s07 权限控制（源脚本遗漏，本项目补齐）
    team/         多智能体协作：messaging、protocols、teammate
    cli/          入口：argparse CLI + REPL
    webui/        入口：FastAPI + 原生 HTML/JS 浏览器端（python -m agents.webui）

对外暴露的高阶 API 见下方 __all__。
"""

# 延迟导入以避免在仅使用常量/工具子集时触发 Anthropic 客户端初始化
__all__ = [
    "agent_loop",          # 主循环（含权限三分支）
    "build_perms",         # PermissionManager 工厂
    "PermissionManager",   # 权限管理器
    "run_repl",            # REPL 入口
    "main",                # CLI 入口
]


def __getattr__(name):  # pragma: no cover - 简单转发
    """
    懒加载：只有真正被访问时才执行对应子模块的 import。

    之所以用模块级 __getattr__ 而不是顶层 `from ... import ...`，
    是因为顶层引入会在 `import agents` 时立即初始化 Anthropic 客户端、
    解析 .env、读取 MODEL_ID 环境变量——这会让 CLI 的 --help 等只读操作
    也要求用户先配好 .env，体验较差。
    """
    if name == "agent_loop":
        from .core.loop import agent_loop
        return agent_loop
    if name == "build_perms":
        from .core.runtime import build_perms
        return build_perms
    if name == "PermissionManager":
        from .permissions.manager import PermissionManager
        return PermissionManager
    if name == "run_repl":
        from .cli.repl import run_repl
        return run_repl
    if name == "main":
        from .cli.main import main
        return main
    raise AttributeError(f"module 'agents' has no attribute {name!r}")
