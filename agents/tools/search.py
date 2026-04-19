"""
tools/search —— 代码搜索工具 `search_content`。

设计：
    * 优先调本机 `rg`（ripgrep）—— 速度快、默认忽略 .gitignore、输出结构化
    * 找不到 rg：回退到 Python 实现（os.walk + re.search）
    * 两种后端**输出格式一致**，LLM 看到的 tool_result 形状稳定

输出形态（与 ripgrep 默认一致的 "path:line:content"）：

    agents/core/loop.py:80:        messages.append({"role": "assistant", ...
    agents/core/loop.py:136:            print(f"> {block.name}: {...}")
    <N more>       # 超过 max_results 时的截断提示

失败/无命中返回一句人类可读的说明，而不是空串——避免 LLM 误以为"工具坏了"。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ..core.config import CONTEXT_TRUNCATE_CHARS, WORKDIR
from .fs import safe_path


# 默认最多返回多少行命中；过多会拖垮上下文
_DEFAULT_MAX_RESULTS: int = 200

# Python 回退遍历时跳过的目录名（和 ripgrep 的默认忽略大致对齐）
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", "__pycache__", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".next", ".nuxt",
    ".codebuddy", ".tasks", ".team", ".transcripts", ".task_outputs",
})

# Python 回退遍历时忽略的文件后缀（二进制/生成物）
_SKIP_SUFFIX: frozenset[str] = frozenset({
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".zip", ".gz", ".tar", ".bz2", ".7z",
    ".pdf", ".mp3", ".mp4", ".mov",
})


def run_search(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    case_sensitive: bool = False,
    max_results: int = _DEFAULT_MAX_RESULTS,
    **_ignored,
) -> str:
    """
    在 WORKDIR 沙箱内按正则搜索代码。

    参数：
        pattern:        正则（Python re 语法；ripgrep 用 PCRE2，但常用子集兼容）
        path:           搜索根（相对 WORKDIR 的路径），默认 "."
        glob:           文件名 glob 过滤，例如 "*.py"；None = 不过滤
        case_sensitive: 是否大小写敏感（默认 False）
        max_results:    最多返回多少行命中（兜底防止结果过大）

    返回：多行字符串，每行形如 "path:line:content"；无命中返回提示信息。
    """
    try:
        root = safe_path(path)
    except Exception as e:
        return f"Error: {e}"
    if not root.exists():
        return f"Error: path not found: {path}"

    # 优先用 ripgrep（更快、忽略 .gitignore）
    rg = shutil.which("rg")
    if rg:
        return _search_with_ripgrep(rg, pattern, root, glob, case_sensitive, max_results)
    return _search_with_python(pattern, root, glob, case_sensitive, max_results)


def _search_with_ripgrep(
    rg: str,
    pattern: str,
    root: Path,
    glob: str | None,
    case_sensitive: bool,
    max_results: int,
) -> str:
    """子进程调 rg；结果截断到 max_results 行。"""
    args: list[str] = [
        rg,
        "--line-number",      # path:line:content 格式
        "--no-heading",       # 平铺，不按文件分组
        "--color=never",
        f"--max-count={max_results}",
    ]
    if not case_sensitive:
        args.append("--ignore-case")
    if glob:
        args.extend(["--glob", glob])
    args.append(pattern)
    args.append(str(root))

    try:
        proc = subprocess.run(
            args,
            capture_output=True, text=True, timeout=30,
            cwd=str(WORKDIR),
        )
    except subprocess.TimeoutExpired:
        return "Error: search timed out after 30s"
    except Exception as e:
        return f"Error: {e}"

    # rg 无命中时 returncode == 1（这是约定，不是错误）
    if proc.returncode not in (0, 1):
        stderr = (proc.stderr or "").strip()
        return f"Error: rg exited with {proc.returncode}: {stderr[:500]}"

    out = proc.stdout or ""
    if not out.strip():
        return "(no matches)"

    lines = out.splitlines()
    # 把绝对路径收敛为相对 WORKDIR 的路径，输出更短
    rel_lines = [_relativize(line) for line in lines]

    truncated = len(rel_lines) > max_results
    if truncated:
        rel_lines = rel_lines[:max_results]
        rel_lines.append(f"... (truncated at {max_results} matches)")

    return "\n".join(rel_lines)[:CONTEXT_TRUNCATE_CHARS]


def _search_with_python(
    pattern: str,
    root: Path,
    glob: str | None,
    case_sensitive: bool,
    max_results: int,
) -> str:
    """
    纯 Python 回退：os.walk + re.search。

    没有 rg 的.gitignore 感知能力，但用 _SKIP_DIRS/_SKIP_SUFFIX 黑名单兜底，
    避免把 node_modules 之类整个翻一遍。
    """
    try:
        regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    from fnmatch import fnmatch

    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地修改 dirnames 可以让 os.walk 跳过黑名单目录
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

        for fname in filenames:
            if Path(fname).suffix.lower() in _SKIP_SUFFIX:
                continue
            if glob and not fnmatch(fname, glob):
                continue

            fpath = Path(dirpath) / fname
            try:
                with fpath.open("r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel = fpath.relative_to(WORKDIR)
                            hits.append(f"{rel}:{lineno}:{line.rstrip()}")
                            if len(hits) >= max_results:
                                hits.append(f"... (truncated at {max_results} matches)")
                                return "\n".join(hits)[:CONTEXT_TRUNCATE_CHARS]
            except (OSError, UnicodeDecodeError):
                continue  # 读不动就跳过（二进制、权限问题等）

    if not hits:
        return "(no matches)"
    return "\n".join(hits)[:CONTEXT_TRUNCATE_CHARS]


def _relativize(line: str) -> str:
    """
    把 "absolute/path:line:content" 变成 "relative/path:line:content"。
    ripgrep 给的是 --no-heading 格式，第一个 ':' 前是路径。
    """
    try:
        path_part, rest = line.split(":", 1)
        abs_path = Path(path_part)
        if abs_path.is_absolute() and str(abs_path).startswith(str(WORKDIR)):
            rel = abs_path.relative_to(WORKDIR)
            return f"{rel}:{rest}"
    except (ValueError, OSError):
        pass
    return line
