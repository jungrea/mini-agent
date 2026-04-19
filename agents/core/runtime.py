"""
core/runtime —— 全局单例与权限管理器工厂。

对应源 s_full.py 第 636–642 行（全局实例集中初始化）。

为什么集中化而不是各模块自己 `TODO = TodoManager()`？
    * dispatch.TOOL_HANDLERS 依赖 TODO/SKILLS/TASK_MGR/BG/BUS/TEAM 六个单例
    * 如果各模块自己创建，会形成 dispatch ↔ managers ↔ team 的循环 import
    * 集中到本模块后：所有"使用方"只 import 这里，"定义方"保持纯函数/类
      —— 这是解耦循环依赖的最轻量方案

PermissionManager 不做成单例，而是提供工厂 build_perms(mode)：
    因为用户可能在 REPL 中 /mode 切换、或通过 CLI --mode 覆盖，
    每次初始化时的 mode 来自运行时输入，不适合在 import 时固化。
"""

from ..managers.background import BackgroundManager
from ..managers.file_tasks import TaskManager
from ..managers.scheduler import CronScheduler
from ..managers.skills import SkillLoader
from ..managers.todos import TodoManager
from ..permissions.manager import PermissionManager
from ..team.messaging import MessageBus
from ..team.teammate import TeammateManager
from .config import SKILLS_DIR


# === 全局单例（整个进程内共享） ===========================================

TODO: TodoManager = TodoManager()
SKILLS: SkillLoader = SkillLoader(SKILLS_DIR)
TASK_MGR: TaskManager = TaskManager()
BG: BackgroundManager = BackgroundManager()
BUS: MessageBus = MessageBus()

# s14: 定时调度器。单例但"懒启动"——实例化几乎零成本（不启后台线程），
# 真正的 start()/stop() 由 REPL 生命周期驱动，保证：
#   * 单轮 CLI（python -m agents run -p "..."）不会起后台线程
#   * 测试 import agents 时不会副作用地拿锁/写 .claude/
CRON: CronScheduler = CronScheduler()

# TEAM 需要 bus / task_mgr；perms 在构造时先传 None，之后 CLI/REPL
# 决定好 mode 再通过 `TEAM.perms = perms` 注入。这样避免 import 时就要求用户选 mode。
TEAM: TeammateManager = TeammateManager(BUS, TASK_MGR, perms=None)


# === 权限管理器工厂 ========================================================

def build_perms(mode: str = "default") -> PermissionManager:
    """
    创建一个新的 PermissionManager。

    约定：
        * 主循环 agent_loop 通过参数接收（不要全局化，利于测试时传替身）
        * 构造后调用方可以：TEAM.perms = perms 让 teammate 循环也能用上
          （teammate 下 ask 会自动退化为 deny，见 team/teammate.py）
    """
    return PermissionManager(mode=mode)
