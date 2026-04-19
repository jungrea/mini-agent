"""
core/prompts —— 系统提示装配器（融合 learn-claude-code-main/agents/s10_system_prompt.py 的思想）。

为什么不再是一行字面量？
    原始版本把整段 SYSTEM 写死为一个 f-string，只塞了"基础指令 + skills 清单"两段。
    s10 的关键教学点是：**system prompt 是一条装配线，不是一坨字符串**。
    本模块把它拆成 6 段 + 一个动态边界标记（DYNAMIC_BOUNDARY），让"稳定前缀"
    和"每轮变化的上下文"在物理上分开——这是做 prompt caching、做测试、做调试
    的前置条件。

6 段结构（前 5 段是稳定前缀，第 6 段是动态后缀）：

    1) Core instructions      —— 角色、工作目录、行为准则（原 SYSTEM 拆出来）
    2) Tool listing           —— 从 dispatch.TOOLS 自动生成，不再双份维护
    3) Skill metadata         —— 复用 managers/skills.py 的 descriptions()
    4) Memory                 —— 扫 .memory/*.md 的 frontmatter + body（存在才读）
    5) CLAUDE.md chain        —— ~/.claude/CLAUDE.md → <workdir>/CLAUDE.md → <cwd>/CLAUDE.md
    ─── DYNAMIC_BOUNDARY ───（以上稳定，以下每轮变）
    6) Dynamic context        —— 日期 / 工作目录 / 平台 / 当前权限模式

对外 API：
    * `SystemPromptBuilder` —— 装配器主类，段落方法各自独立便于测试
    * `BUILDER` —— 进程级默认实例；`agent_loop` 每轮用它重建 dynamic 段
    * `SYSTEM` —— 兼容老调用点（`from .prompts import SYSTEM`）；导入时立刻装配
                   **一次**稳定前缀 + **首次**的 dynamic 段，loop 会按需重建覆盖
    * `build_identity(name, role, team, workdir)` —— teammate 字面量共享的身份片段
    * `list_sections(...)` —— /sections 斜杠命令用；只列段落标题，不打印内容
"""

from __future__ import annotations

import platform as _platform
import re
import sys
from datetime import datetime
from pathlib import Path

from .config import WORKDIR

# 注意：skills / tools 都走延迟 import（方法内 import），
# 避免 `prompts → runtime → team/teammate → prompts` 的循环导入。
# `runtime.py` 在 import 期就要构造 TEAM，而 TeammateManager 需要 build_identity，
# 此时 prompts 若在顶层 import runtime 会陷入半初始化状态。


# 稳定前缀与动态后缀之间的分隔标记。s10 原版保留的这个 marker 是为了
# 让未来做 prompt caching 时能在这里切一刀（前半段可缓存，后半段每轮重建）。
# 本项目当前不做实际切片，但保留标记成本为零。
DYNAMIC_BOUNDARY: str = "=== DYNAMIC_BOUNDARY ==="


# ============================================================================
# 工具：memory / CLAUDE.md 扫描
# ============================================================================

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    解析 Markdown frontmatter（与 managers/skills.py 同款正则）。

    复刻而非复用，是为了解耦——skills 模块与 prompts 模块不应互相依赖。
    匹配 `---\\n...\\n---\\n` 头块，返回 (meta_dict, body)；无 frontmatter
    时 meta 为空 dict，body 为原文。
    """
    match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text
    meta: dict = {}
    for line in match.group(1).strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, match.group(2).strip()


def _scan_memory(memory_dir: Path) -> list[dict]:
    """
    扫描 `.memory/*.md`，返回每条记忆的 meta + body。

    设计取舍：
        * 目录不存在 → 返回空列表，不报错（保持"零配置启动"）
        * 只认顶层 `.md`（不递归），避免与 `.memory/.git/` 或嵌套笔记冲突
        * 读取失败单文件跳过，不影响整体装配
    """
    if not memory_dir.exists() or not memory_dir.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(memory_dir.glob("*.md")):
        try:
            text = f.read_text()
        except OSError:
            continue
        meta, body = _parse_frontmatter(text)
        out.append({
            "name": meta.get("name", f.stem),
            "type": meta.get("type", "note"),
            "description": meta.get("description", ""),
            "body": body,
            "path": str(f),
        })
    return out


def _claude_md_chain() -> list[tuple[str, Path]]:
    """
    返回 CLAUDE.md 查找链，顺序：global → workdir → cwd。

    每项是 (tag, path)，tag 用来在装配时标注来源（便于 LLM 和人类调试）。
    同一路径重复只保留第一次出现（比如 WORKDIR == cwd 时 cwd 会被去重）。
    """
    candidates: list[tuple[str, Path]] = [
        ("global",  Path.home() / ".claude" / "CLAUDE.md"),
        ("workdir", WORKDIR / "CLAUDE.md"),
        ("cwd",     Path.cwd() / "CLAUDE.md"),
    ]
    seen: set[Path] = set()
    out: list[tuple[str, Path]] = []
    for tag, p in candidates:
        rp = p.resolve() if p.exists() else p
        if rp in seen:
            continue
        seen.add(rp)
        out.append((tag, p))
    return out


# ============================================================================
# 工具：按 dispatch.TOOLS 自动生成工具段
# ============================================================================

# 工具分组：让"30+ 个工具"在 prompt 里按功能聚成几堆，可读性远好于一长串列表。
# 未在分组里登记的工具会被归到 "Other"（例如未来新增的工具）。
_TOOL_GROUPS: list[tuple[str, list[str]]] = [
    ("File I/O",       ["read_file", "write_file", "edit_file"]),
    ("Shell",          ["bash", "background_run", "check_background"]),
    ("Search",         ["search_content"]),
    ("Web",            ["web_fetch", "web_search"]),
    ("Planning",       ["TodoWrite", "task_create", "task_get", "task_update",
                        "task_list", "claim_task"]),
    ("Delegation",     ["task", "spawn_teammate", "list_teammates"]),
    ("Messaging",      ["send_message", "read_inbox", "broadcast"]),
    ("Protocol",       ["shutdown_request", "plan_approval", "idle"]),
    ("Skills",         ["load_skill"]),
    ("Context",        ["compress"]),
]


def _tool_param_hint(schema: dict) -> str:
    """
    从 JSON Schema 抽出人类可读的参数摘要，如 "(command, timeout?)"。

    只展示 properties 里的字段；required 字段原样，optional 字段带 "?"。
    param 字段的 description 略过——放在段落里会把 prompt 撑爆，工具真正的
    description 已单独渲染。
    """
    props = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    if not props:
        return "()"
    parts = [p if p in required else f"{p}?" for p in props.keys()]
    return "(" + ", ".join(parts) + ")"


def _render_tool_listing(tools: list[dict]) -> str:
    """
    把 TOOLS list 渲染成分组的可读清单。

    输出形如：
        ## File I/O
          - read_file(path, limit?): Read file contents.
          - write_file(path, content): Write content to file.
        ## Shell
          - bash(command): Run a shell command.
          ...
    """
    by_name: dict[str, dict] = {t["name"]: t for t in tools}
    used: set[str] = set()
    lines: list[str] = []

    for group_name, names in _TOOL_GROUPS:
        group_lines: list[str] = []
        for n in names:
            t = by_name.get(n)
            if not t:
                continue  # 该工具在本项目里没注册（容错）
            used.add(n)
            params = _tool_param_hint(t.get("input_schema") or {})
            desc = (t.get("description") or "").strip()
            # 描述可能很长（search_content / web_search 都是多行），压到一行
            desc = " ".join(desc.split())
            group_lines.append(f"  - {n}{params}: {desc}")
        if group_lines:
            lines.append(f"## {group_name}")
            lines.extend(group_lines)

    # 兜底组：不在任何预设分组里的工具
    leftover = [t for t in tools if t["name"] not in used]
    if leftover:
        lines.append("## Other")
        for t in leftover:
            params = _tool_param_hint(t.get("input_schema") or {})
            desc = " ".join((t.get("description") or "").split())
            lines.append(f"  - {t['name']}{params}: {desc}")

    return "\n".join(lines)


# ============================================================================
# 身份片段（teammate 复用）
# ============================================================================

def build_identity(name: str, role: str, team: str, workdir: Path | str) -> str:
    """
    构造"你是谁"的身份段。

    lead 和 teammate 共享同一句式，减少措辞分叉；本函数是整个 prompts 模块
    唯一被 `team/teammate.py` 反向引用的 API（最小耦合）。
    """
    return (
        f"You are '{name}', role: {role}, team: {team}, at {workdir}. "
        f"Use idle when done with current work. You may auto-claim tasks."
    )


# ============================================================================
# SystemPromptBuilder
# ============================================================================

class SystemPromptBuilder:
    """
    把系统提示拆成 6 段，按需装配。

    使用方式：
        builder = SystemPromptBuilder()
        system_text = builder.build(mode="default")          # 完整 system
        static_prefix = builder.build_static()               # 只要稳定前缀（缓存用）
        dynamic_only  = builder.build_dynamic(mode="plan")   # 只要动态段（调试/reminder 用）

    每个 `_build_*` 方法独立、可单测。段落按"存在性"自动开合——
    skills 为空则跳过 skills 段，.memory/ 不存在则跳过 memory 段，依此类推。
    """

    def __init__(
        self,
        workdir: Path | None = None,
        memory_dir: Path | None = None,
        tools: list[dict] | None = None,
    ):
        """
        参数全部可选，默认取全局配置：
            * workdir    —— 沙箱根（config.WORKDIR）
            * memory_dir —— `.memory/` 目录位置（默认 workdir/.memory）
            * tools      —— 工具 schema；默认延迟从 dispatch.TOOLS 取
        """
        self.workdir: Path = workdir or WORKDIR
        self.memory_dir: Path = memory_dir or (self.workdir / ".memory")
        self._tools_override = tools

    # ------------------------------------------------------------------
    # 延迟取 TOOLS：避免 core.prompts 顶层 import core.dispatch
    # 造成循环（dispatch → runtime → ... → prompts）。
    # ------------------------------------------------------------------
    def _tools(self) -> list[dict]:
        if self._tools_override is not None:
            return self._tools_override
        from .dispatch import TOOLS  # 延迟 import
        return TOOLS

    # ------------------------------------------------------------------
    # 六段
    # ------------------------------------------------------------------
    def _build_core(self) -> str:
        """段 1：核心指令。保留原 SYSTEM 的行为准则，仅改为完整段落。"""
        return (
            "# Core instructions\n"
            f"You are a coding agent at {self.workdir}. Use tools to solve tasks.\n"
            "Prefer task_create/task_update/task_list for multi-step work.\n"
            "Use TodoWrite for short checklists.\n"
            "Use task for subagent delegation. Use load_skill for specialized knowledge.\n"
            "For locating code, prefer search_content over bash grep/find "
            "(faster and returns path:line:content).\n"
            "For reading web pages use web_fetch; for searching the web use web_search. "
            "Do not invoke curl/wget via bash."
        )

    def _build_tools(self) -> str:
        """段 2：工具清单，从 dispatch.TOOLS 自动生成。"""
        body = _render_tool_listing(self._tools())
        return "# Available tools\n" + body

    def _build_skills(self) -> str:
        """段 3：技能元信息。技能为空时返回空串（整段省略）。"""
        from .runtime import SKILLS  # 延迟 import：避免与 teammate 产生循环
        if not SKILLS.skills:
            return ""
        return "# Available skills\n" + SKILLS.descriptions()

    def _build_memory(self) -> str:
        """段 4：记忆。目录不存在或空时返回空串。"""
        items = _scan_memory(self.memory_dir)
        if not items:
            return ""
        lines = ["# Memory"]
        for m in items:
            # 每条记忆以"[type] name: description"做 header，body 随后
            header = f"## [{m['type']}] {m['name']}"
            if m["description"]:
                header += f" — {m['description']}"
            lines.append(header)
            if m["body"]:
                lines.append(m["body"])
        return "\n".join(lines)

    def _build_claude_md(self) -> str:
        """段 5：CLAUDE.md 三层链，全不存在则返回空串。"""
        blocks: list[str] = []
        for tag, path in _claude_md_chain():
            if not path.exists():
                continue
            try:
                text = path.read_text().strip()
            except OSError:
                continue
            if not text:
                continue
            blocks.append(f"## CLAUDE.md ({tag}: {path})\n{text}")
        if not blocks:
            return ""
        return "# Project instructions\n" + "\n\n".join(blocks)

    def _build_dynamic(self, mode: str | None = None) -> str:
        """
        段 6：动态上下文。包含随每轮变化的信息——日期、cwd、平台、权限模式。

        mode 不传时省略权限模式行；想"纯静态"场景（测试、缓存）可以传 None。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            "# Runtime context",
            f"Date: {now}",
            f"Workdir: {self.workdir}",
            f"Cwd: {Path.cwd()}",
            f"Platform: {_platform.system()} {_platform.release()} / "
            f"Python {sys.version.split()[0]}",
        ]
        if mode:
            lines.append(f"Permission mode: {mode}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 组装入口
    # ------------------------------------------------------------------
    def build_static(self) -> str:
        """只装配稳定前缀（前 5 段）。未来若做 prompt caching，缓存的就是它。"""
        sections = [
            self._build_core(),
            self._build_tools(),
            self._build_skills(),
            self._build_memory(),
            self._build_claude_md(),
        ]
        return "\n\n".join(s for s in sections if s)

    def build_dynamic(self, mode: str | None = None) -> str:
        """只装配第 6 段。loop 里每轮都会调它。"""
        return self._build_dynamic(mode=mode)

    def build(self, mode: str | None = None) -> str:
        """完整装配：静态前缀 + DYNAMIC_BOUNDARY + 动态后缀。"""
        static = self.build_static()
        dynamic = self.build_dynamic(mode=mode)
        return f"{static}\n\n{DYNAMIC_BOUNDARY}\n\n{dynamic}"

    # ------------------------------------------------------------------
    # 段落索引（/sections 命令用）
    # ------------------------------------------------------------------
    def list_sections(self, mode: str | None = None) -> list[tuple[str, bool, int]]:
        """
        返回 [(段名, 是否启用, 字符数)] —— 供 /sections 快速观察。

        "是否启用"体现"存在性开合"：memory 为空时 enabled=False。
        """
        items: list[tuple[str, str]] = [
            ("1. Core",       self._build_core()),
            ("2. Tools",      self._build_tools()),
            ("3. Skills",     self._build_skills()),
            ("4. Memory",     self._build_memory()),
            ("5. CLAUDE.md",  self._build_claude_md()),
            ("6. Dynamic",    self._build_dynamic(mode=mode)),
        ]
        return [(name, bool(body), len(body)) for name, body in items]


# ============================================================================
# 进程级默认实例 + 兼容导出
# ============================================================================

# 进程级默认实例：整个进程共用。loop / repl / 斜杠命令都从这里取。
BUILDER: SystemPromptBuilder = SystemPromptBuilder()


# 兼容老调用点 `from .prompts import SYSTEM`：用模块级 __getattr__ 做**懒装配**。
#   * 不能在 import 时立刻 `BUILDER.build()`，否则会在 import 链路中途
#     要求 `dispatch.TOOLS`，而 `dispatch` 可能尚未完成初始化（循环依赖陷阱）。
#   * 懒装配：第一次 `from .prompts import SYSTEM` 或 `prompts.SYSTEM` 时触发装配，
#     此时 `dispatch` 早已完成初始化。
#   * `agent_loop` 本身不再用 SYSTEM（它直接调 `BUILDER.build(mode=...)` 每轮重建），
#     这个常量现在更像"向后兼容的导出"+"教学参考"。
_SYSTEM_CACHE: str | None = None


def __getattr__(name: str) -> str:  # PEP 562
    global _SYSTEM_CACHE
    if name == "SYSTEM":
        if _SYSTEM_CACHE is None:
            _SYSTEM_CACHE = BUILDER.build()
        return _SYSTEM_CACHE
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DYNAMIC_BOUNDARY",
    "SystemPromptBuilder",
    "BUILDER",
    "SYSTEM",
    "build_identity",
]
