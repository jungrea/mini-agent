"""core —— 编排层。

config: 环境变量 / Anthropic 客户端 / 路径与阈值常量
prompts: 系统提示 SYSTEM
runtime: 全局单例 + 权限管理器工厂
dispatch: TOOL_HANDLERS 与 TOOLS schema
loop: agent_loop（含权限三分支）
"""
