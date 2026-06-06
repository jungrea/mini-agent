"""
managers/memory —— s09 记忆系统：跨压缩、跨会话的知识层。

双层存储设计（对照 learn-claude-code/s09_memory，扩展为 user + project 两层）：

    ~/.claude/memory/                ← 用户级（跨项目共享）
      MEMORY.md                        索引
      user-pref-tabs.md                type=user 的记忆都落到这里

    <WORKDIR>/.memory/               ← 项目级（仅本项目可见）
      MEMORY.md                        索引
      project-auth-rewrite.md          type ∈ {feedback, project, reference}

为什么这样路由：
    * "我用 tab 不用空格"是关于"我"的事实，换个项目仍然成立 → 用户级
    * "别 mock 这个项目的数据库"只在本项目成立 → 项目级
    * type 字段已经在 frontmatter 里区分内容性质，正好用来做 scope 路由，
      不需要额外引入 scope 字段，提取 prompt 也不用改。

读取与注入：
    * read_memory_index() 合并两层，每段加 `## (scope: user|project)` header
    * load_memories() 用 LLM side-query 选 ≤5 条相关项，跨 scope 联合编号
    * 重名（user/foo.md 和 project/foo.md 同名）允许并存——内部用
      `(scope, filename)` 二元组定位，外部 API 看到的也是这个组合

写入：
    * write_memory_file 按 mem_type 自动路由 scope
    * 每个 scope 维护自己的 MEMORY.md 索引

整理：
    * consolidate_memories() 对两个 scope 各跑一次，独立锁、独立阈值
    * 用户级文件锁在 ~/.claude/memory/.consolidate-lock，防多进程同时跑

总开关：MEMORY_ENABLED=0 → 整套静默。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from ..core.config import CURRENT_WORKDIR, MODEL, WORKDIR, client


# ============================================================================
# 配置 / 常量
# ============================================================================

def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# 总开关。默认开启；设 MEMORY_ENABLED=0 整套静默（不读、不写、不抓）。
MEMORY_ENABLED: bool = _env_bool("MEMORY_ENABLED", default=True)

# 记忆类型枚举。type=user 路由到用户级，其它路由到项目级。
MEMORY_TYPES = ("user", "feedback", "project", "reference")
USER_SCOPED_TYPES = frozenset({"user"})

# scope 名（出现在索引 header / 公开 API）
SCOPE_USER = "user"
SCOPE_PROJECT = "project"
SCOPES = (SCOPE_USER, SCOPE_PROJECT)

# 整理触发阈值：单个 scope 内文件数 ≥ 此值才跑 consolidate
CONSOLIDATE_THRESHOLD: int = 10

# select_relevant_memories 单次最多返回的记忆条数（跨 scope 合计）
MAX_RELEVANT: int = 5

# 锁文件最大有效期（秒）。consolidate 持锁期间崩溃 → 1 小时后锁自动失效。
LOCK_TTL_SECONDS: int = 3600


# ============================================================================
# 路径解析
# ============================================================================

def _active_workdir() -> Path:
    """项目级跟随 CURRENT_WORKDIR（webui 多会话独立目录），否则用 WORKDIR。"""
    cw = CURRENT_WORKDIR.get()
    return cw if cw is not None else WORKDIR


def _user_memory_dir() -> Path:
    """
    用户级记忆目录：~/.claude/memory/
    与 ~/.claude/CLAUDE.md（全局指令）同级，便于一处管理。
    """
    return Path.home() / ".claude" / "memory"


def _project_memory_dir() -> Path:
    """项目级记忆目录：<active_workdir>/.memory/"""
    return _active_workdir() / ".memory"


def _scope_dir(scope: str) -> Path:
    if scope == SCOPE_USER:
        return _user_memory_dir()
    return _project_memory_dir()


def _scope_index_path(scope: str) -> Path:
    return _scope_dir(scope) / "MEMORY.md"


def _ensure_scope_dir(scope: str) -> Path:
    d = _scope_dir(scope)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _scope_for_type(mem_type: str) -> str:
    """type=user → user scope；其它一律 project scope。"""
    return SCOPE_USER if mem_type in USER_SCOPED_TYPES else SCOPE_PROJECT


# ============================================================================
# Frontmatter 解析（独立复刻，不依赖 prompts/skills）
# ============================================================================

def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, parts[2].strip()


def _extract_text(content) -> str:
    """从 Anthropic response.content 抽取所有 text 块拼成字符串。"""
    if not isinstance(content, list):
        return str(content)
    return "\n".join(
        getattr(b, "text", "") for b in content
        if getattr(b, "type", None) == "text"
    )


# ============================================================================
# 读：list / read / index（按 scope 或合并）
# ============================================================================

def _list_files_in_scope(scope: str) -> list[dict]:
    """列单个 scope 下的记忆元信息。"""
    d = _scope_dir(scope)
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            raw = f.read_text()
        except OSError:
            continue
        meta, body = _parse_frontmatter(raw)
        out.append({
            "scope": scope,
            "filename": f.name,
            "name": meta.get("name", f.stem),
            "description": meta.get("description", ""),
            "type": meta.get("type", "user" if scope == SCOPE_USER else "project"),
            "body": body,
        })
    return out


def list_memory_files() -> list[dict]:
    """
    列出所有记忆元信息（user 在前，project 在后，便于阅读）。
    每条多一个 `scope` 字段（"user" / "project"）。
    """
    if not MEMORY_ENABLED:
        return []
    return _list_files_in_scope(SCOPE_USER) + _list_files_in_scope(SCOPE_PROJECT)


def read_memory_index() -> str:
    """
    读两层 MEMORY.md 索引并合并。两层都空则返回空串。
    输出格式：

        ## Memory (scope: user — global)
        - [user-pref-tabs](user-pref-tabs.md) — Use tabs

        ## Memory (scope: project — this workdir)
        - [project-auth-rewrite](project-auth-rewrite.md) — ...

    没启用、两层都空时返回空串（让 prompts 整段省略）。
    """
    if not MEMORY_ENABLED:
        return ""

    sections: list[str] = []
    headers = {
        SCOPE_USER:    "## Memory (scope: user — global, ~/.claude/memory)",
        SCOPE_PROJECT: "## Memory (scope: project — this workdir, .memory/)",
    }
    for scope in SCOPES:
        p = _scope_index_path(scope)
        if not p.exists():
            continue
        try:
            text = p.read_text().strip()
        except OSError:
            continue
        if not text:
            continue
        sections.append(headers[scope] + "\n" + text)
    return "\n\n".join(sections)


def read_memory_file(filename: str, scope: Optional[str] = None) -> Optional[str]:
    """
    按文件名读单条记忆全文（含 frontmatter）。

    scope:
        * 显式传 "user" / "project" → 只在该 scope 找
        * None → 先找 user，再找 project（命中即返回）
    """
    if not MEMORY_ENABLED:
        return None
    scopes_to_try = (scope,) if scope else SCOPES
    for s in scopes_to_try:
        p = _scope_dir(s) / filename
        if p.exists():
            try:
                return p.read_text()
            except OSError:
                continue
    return None


# ============================================================================
# 写：write_memory_file + 重建索引（per-scope）
# ============================================================================

def _rebuild_index(scope: str) -> None:
    """按当前文件列表重建指定 scope 的 MEMORY.md。"""
    d = _ensure_scope_dir(scope)
    lines: list[str] = []
    for f in sorted(d.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            raw = f.read_text()
        except OSError:
            continue
        meta, body = _parse_frontmatter(raw)
        name = meta.get("name", f.stem)
        desc = meta.get("description") or (body.split("\n")[0][:80] if body else "")
        lines.append(f"- [{name}]({f.name}) — {desc}")
    content = "\n".join(lines) + "\n" if lines else ""
    _scope_index_path(scope).write_text(content)


def write_memory_file(name: str, mem_type: str, description: str, body: str) -> Optional[Path]:
    """
    写一条记忆，按 mem_type 自动路由 scope，写完重建该 scope 的索引。

    路由规则：type='user' → 用户级；其它 → 项目级。
    返回写入路径；MEMORY_ENABLED=0 / 入参不全 时返回 None。
    """
    if not MEMORY_ENABLED:
        return None
    if not name or not body:
        return None
    if mem_type not in MEMORY_TYPES:
        mem_type = "user"
    scope = _scope_for_type(mem_type)
    d = _ensure_scope_dir(scope)
    slug = name.lower().replace(" ", "-").replace("/", "-")
    filepath = d / f"{slug}.md"
    filepath.write_text(
        f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{body}\n"
    )
    _rebuild_index(scope)
    return filepath


# ============================================================================
# 选：相关记忆筛选（LLM side-query + 关键词降级）
# ============================================================================

def _recent_user_text(messages: list, max_msgs: int = 3, max_chars: int = 2000) -> str:
    """从 messages 末尾收集最多 max_msgs 条 user 文本，反序拼接。"""
    pieces: list[str] = []
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    text_parts.append(str(b.get("text", "")))
                elif getattr(b, "type", None) == "text":
                    text_parts.append(str(getattr(b, "text", "")))
            content = " ".join(text_parts)
        if isinstance(content, str) and content.strip():
            pieces.append(content)
        if len(pieces) >= max_msgs:
            break
    return " ".join(reversed(pieces))[:max_chars]


def select_relevant_memories(messages: list, max_items: int = MAX_RELEVANT) -> list[tuple[str, str]]:
    """
    根据最近对话，跨 scope 选出相关记忆。

    返回：[(scope, filename), ...]，长度 ≤ max_items。
    LLM 失败时降级关键词匹配；MEMORY_ENABLED=0 / 无候选 / 无 user 文本 → 空。
    """
    if not MEMORY_ENABLED:
        return []
    files = list_memory_files()
    if not files:
        return []
    recent = _recent_user_text(messages)
    if not recent.strip():
        return []

    # 跨 scope 联合编号 0..N-1，每行标注 scope 让 LLM 知道"哪些是全局偏好哪些是本项目"
    catalog = "\n".join(
        f"{i}: [{f['scope']}/{f['type']}] {f['name']} — {f['description']}"
        for i, f in enumerate(files)
    )

    prompt = (
        "Given the recent conversation and the memory catalog below, "
        "select the indices of memories that are clearly relevant. "
        "Return ONLY a JSON array of integers, e.g. [0, 3]. "
        "If none are relevant, return [].\n\n"
        f"Recent conversation:\n{recent}\n\n"
        f"Memory catalog:\n{catalog}"
    )

    # 路径一：LLM side-query
    try:
        response = client.messages.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )
        text = _extract_text(response.content).strip()
        match = re.search(r"\[.*?\]", text, re.DOTALL)
        if match:
            indices = json.loads(match.group())
            selected: list[tuple[str, str]] = []
            for idx in indices:
                if isinstance(idx, int) and 0 <= idx < len(files):
                    f = files[idx]
                    selected.append((f["scope"], f["filename"]))
                    if len(selected) >= max_items:
                        break
            if selected:
                return selected
    except Exception:
        pass

    # 路径二：关键词降级（match name/description；scope 信息保留）
    keywords = [w.lower() for w in recent.split() if len(w) > 3]
    selected2: list[tuple[str, str]] = []
    for f in files:
        hay = (f["name"] + " " + f["description"]).lower()
        if any(kw in hay for kw in keywords):
            selected2.append((f["scope"], f["filename"]))
            if len(selected2) >= max_items:
                break
    return selected2


def load_memories(messages: list) -> str:
    """
    选出相关记忆，拼成 <relevant_memories>...</relevant_memories>。
    内部按 scope 顺序排列（user 在前），保持注入稳定。
    """
    if not MEMORY_ENABLED:
        return ""
    selected = select_relevant_memories(messages)
    if not selected:
        return ""
    # user scope 在前
    selected_sorted = sorted(selected, key=lambda sf: 0 if sf[0] == SCOPE_USER else 1)
    parts = ["<relevant_memories>"]
    for scope, filename in selected_sorted:
        content = read_memory_file(filename, scope=scope)
        if content:
            parts.append(f"<!-- scope: {scope} -->\n{content}")
    parts.append("</relevant_memories>")
    return "\n\n".join(parts)


# ============================================================================
# 抓：extract_memories（RoundEnd 调）
# ============================================================================

def _format_dialogue(messages: list, tail: int = 10) -> str:
    """把最近 tail 条消息渲染成 'role: text' 串，供提取 prompt 使用。"""
    out: list[str] = []
    for msg in messages[-tail:]:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for b in content:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        text_parts.append(str(b.get("text", "")))
                elif getattr(b, "type", None) == "text":
                    text_parts.append(str(getattr(b, "text", "")))
            content = " ".join(text_parts)
        if isinstance(content, str) and content.strip():
            out.append(f"{role}: {content}")
    return "\n".join(out)


def extract_memories(messages: list) -> list[dict]:
    """
    从最近对话中抽取新记忆。

    返回：
        list[dict]，每项 {"name": str, "scope": str, "type": str}。
        空列表表示什么都没写。返回 dict 而不是数量是为了让上层（loop / webui）
        能在 progress 通知里给用户看到"具体记了什么"——透明度比简洁重要。

    抽取 prompt 要求 LLM 自己判断 type；scope 由 write_memory_file 按 type 路由。
    "用户偏好"自动落到用户级，"项目事实"自动落到项目级——LLM 不需要懂 scope 概念。
    """
    if not MEMORY_ENABLED:
        return []
    dialogue = _format_dialogue(messages)
    if not dialogue.strip():
        return []

    existing = list_memory_files()
    existing_desc = (
        "\n".join(f"- [{m['scope']}] {m['name']}: {m['description']}" for m in existing)
        if existing else "(none)"
    )

    prompt = (
        "Extract user preferences, constraints, or project facts from this dialogue.\n"
        "Return a JSON array. Each item: {name, type, description, body}.\n"
        "- name: short kebab-case identifier (e.g. 'user-preference-tabs')\n"
        "- type: one of 'user' (user preference; cross-project), "
        "'feedback' (guidance for this work), "
        "'project' (project fact), 'reference' (external pointer)\n"
        "- description: one-line summary for index lookup\n"
        "- body: full detail in markdown\n"
        "Use 'user' ONLY for preferences that hold across projects (style, tone, "
        "tooling habits). Use 'project'/'feedback'/'reference' for things tied to "
        "this specific codebase.\n"
        "If nothing new or already covered by existing memories, return [].\n\n"
        f"Existing memories:\n{existing_desc}\n\n"
        f"Dialogue:\n{dialogue[:4000]}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        text = _extract_text(response.content).strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        items = json.loads(match.group())
        if not items:
            return []
        written: list[dict] = []
        for mem in items:
            name = mem.get("name") or f"memory-{int(time.time())}"
            mem_type = mem.get("type", "user")
            desc = mem.get("description", "")
            body = mem.get("body", "")
            if desc and body:
                path = write_memory_file(name, mem_type, desc, body)
                if path:
                    written.append({
                        "name": name,
                        "scope": _scope_for_type(mem_type),
                        "type": mem_type,
                    })
        if written:
            scope_tag = "+".join(sorted({w["scope"] for w in written}))
            print(f"\033[33m[Memory: extracted {len(written)} new memories → {scope_tag}]\033[0m")
        return written
    except Exception:
        return []


# ============================================================================
# 整理：consolidate_memories（per-scope 锁）
# ============================================================================

def _try_acquire_lock(scope: str) -> Optional[Path]:
    """每个 scope 一把锁，互不影响。"""
    d = _ensure_scope_dir(scope)
    lock_path = d / ".consolidate-lock"
    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
        except OSError:
            age = 0
        if age < LOCK_TTL_SECONDS:
            return None
        try:
            lock_path.unlink()
        except OSError:
            return None
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return lock_path
    except FileExistsError:
        return None
    except OSError:
        return None


def _release_lock(lock_path: Optional[Path]) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


def _consolidate_scope(scope: str) -> int:
    """对单个 scope 跑一次合并。返回新数量；未触发/失败返回 -1。"""
    files = _list_files_in_scope(scope)
    if len(files) < CONSOLIDATE_THRESHOLD:
        return -1

    lock = _try_acquire_lock(scope)
    if lock is None:
        return -1

    try:
        catalog = "\n\n".join(
            f"## {f['filename']}\nname: {f['name']}\n"
            f"description: {f['description']}\ntype: {f['type']}\n{f['body']}"
            for f in files
        )
        scope_hint = (
            "These are USER-level memories (cross-project preferences). "
            "Keep type='user' on every output item."
            if scope == SCOPE_USER
            else "These are PROJECT-level memories (this codebase only). "
                 "Use type ∈ {feedback, project, reference}; do NOT downgrade to 'user'."
        )
        prompt = (
            "Consolidate the following memory files. Rules:\n"
            "1. Merge duplicates into one\n"
            "2. Remove outdated/contradicted memories\n"
            "3. Keep the total under 30 memories\n"
            "4. Preserve important user preferences above all\n"
            f"5. {scope_hint}\n"
            "Return a JSON array. Each item: {name, type, description, body}.\n\n"
            f"{catalog[:16000]}"
        )
        response = client.messages.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
        )
        text = _extract_text(response.content).strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return -1
        items = json.loads(match.group())
        if not items:
            return -1

        # 删除该 scope 下的旧文件（保留 MEMORY.md，等 _rebuild_index 重写）
        d = _scope_dir(scope)
        for f in d.glob("*.md"):
            if f.name == "MEMORY.md":
                continue
            try:
                f.unlink()
            except OSError:
                pass

        # 写回：write_memory_file 会按 type 路由——这里强制留在本 scope，
        # 否则 LLM 把 project 类记忆误标成 user 会把它"漂移"到全局目录。
        kept = 0
        forced_type = "user" if scope == SCOPE_USER else None
        for mem in items:
            name = mem.get("name") or f"memory-{int(time.time())}-{kept}"
            mem_type = mem.get("type", "user")
            if forced_type is not None:
                mem_type = forced_type
            elif mem_type == "user":
                # 项目 scope 不允许 user type，否则会路由到全局——降级为 project
                mem_type = "project"
            desc = mem.get("description", "")
            body = mem.get("body", "")
            if desc and body and write_memory_file(name, mem_type, desc, body):
                kept += 1
        print(
            f"\033[33m[Memory: consolidated {scope} {len(files)} → {kept} memories]\033[0m"
        )
        return kept
    except Exception:
        return -1
    finally:
        _release_lock(lock)


def consolidate_memories() -> dict[str, int]:
    """
    对两个 scope 各跑一次合并。返回 {scope: kept_count}；未触发的 scope 不出现。

    user / project 两层独立判定阈值、独立持锁——一个 scope 在整理时不
    阻塞另一个。
    """
    if not MEMORY_ENABLED:
        return {}
    out: dict[str, int] = {}
    for scope in SCOPES:
        n = _consolidate_scope(scope)
        if n >= 0:
            out[scope] = n
    return out


__all__ = [
    "MEMORY_ENABLED",
    "MEMORY_TYPES",
    "USER_SCOPED_TYPES",
    "SCOPE_USER",
    "SCOPE_PROJECT",
    "SCOPES",
    "CONSOLIDATE_THRESHOLD",
    "list_memory_files",
    "read_memory_index",
    "read_memory_file",
    "write_memory_file",
    "select_relevant_memories",
    "load_memories",
    "extract_memories",
    "consolidate_memories",
]
