"""
api/fs_browse —— 极简服务端目录浏览 REST。

定位：
    给"新建会话"对话框里的"📂 浏览"按钮提供后端支持。前端弹一个二级
    模态框，列服务端的目录树，让用户用点击代替手动粘贴绝对路径。

为什么要服务端？
    浏览器出于安全模型不暴露用户机器的绝对路径（即便 File System
    Access API 拿到的是不可序列化的 handle，也不是路径字符串）。
    所以"目录浏览器"必须由后端枚举服务端文件系统，前端只负责展示。
    这与 VS Code、Jupyter 等本地化工具的做法一致。

设计取舍：
    * **只列目录、不列文件**：减少噪声 + 缩小返回 payload；用户选的也是目录
    * **绝对路径返回前端**：与 webui/session.py 的 validate_workdir 保持
      契约一致——前端拿到的字符串可以直接作为 `workdir` 字段提交
    * **不列敏感目录**：与 session.py 的 _SENSITIVE_PREFIXES 黑名单同步，
      但这里采取"允许进入查看，但拒绝把它选为 workdir"的策略——浏览只读，
      真正的写权限校验仍在 validate_workdir 里把关
    * **沿用 hidden_filter**：默认隐藏 . 开头的目录（.git / .venv 等），
      可由 query 参数关闭
    * **错误兜底**：路径不存在 / 权限不足 / 不是目录 → 返回错误而非 500

接口：
    GET  /api/fs/list?path=<absolute_path>&show_hidden=<0|1>
        返回 {
            "path": <规范化后的绝对路径>,
            "parent": <上一级路径或 null>,
            "entries": [{"name": "...", "path": "...", "readable": true}, ...]
        }
    GET  /api/fs/home
        返回 {"path": "<HOME>"}  方便前端打开浏览器时定位到默认起始目录
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/api/fs")


def _safe_abs(raw: str) -> Path:
    """
    把前端传入的 path 解析为绝对 Path。

    * 展开 ~ 与 $VAR
    * 不强制 strict=True（让"路径不存在"这种错由调用方决定如何报）
    * 不做"不能逃出 X"之类校验——浏览本身允许去任何位置看看，
      只在 validate_workdir 那一步限制能不能"用"作 workdir
    """
    raw = (raw or "").strip()
    if not raw:
        raw = str(Path.home())
    return Path(os.path.expandvars(raw)).expanduser().resolve(strict=False)


@router.get("/home")
def fs_home():
    """返回用户家目录的绝对路径，前端"打开浏览器"时用作默认起点。"""
    return {"path": str(Path.home())}


@router.get("/list")
def fs_list(
    path: str = Query("", description="绝对路径；空字符串 = $HOME"),
    show_hidden: int = Query(0, description="1=列出 . 开头的目录"),
    limit: int = Query(500, description="单次最多返回多少子目录，防止巨型目录拖死前端"),
):
    """
    列出 `path` 下的子目录。

    返回结构在模块 docstring 顶部有完整例子。错误情况：
        * 路径不存在 → 404
        * 路径不是目录 → 400
        * 路径无读权限 → 403
    """
    p = _safe_abs(path)
    if not p.exists():
        raise HTTPException(404, f"path not found: {p}")
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {p}")
    if not os.access(p, os.R_OK):
        raise HTTPException(403, f"permission denied: {p}")

    entries: list[dict] = []
    try:
        # scandir 比 listdir + stat 一次次调要快得多；is_dir() 也避免双 syscall
        with os.scandir(p) as it:
            for entry in it:
                name = entry.name
                if not show_hidden and name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    # 软链断了 / 权限不足读不到 dirent 元数据 → 跳过
                    continue
                full = (p / name).resolve(strict=False)
                entries.append({
                    "name": name,
                    "path": str(full),
                    # readable=False 的目录前端可以渲染成灰色不可点
                    "readable": os.access(full, os.R_OK | os.X_OK),
                })
    except PermissionError:
        raise HTTPException(403, f"permission denied while listing: {p}") from None
    except OSError as e:
        raise HTTPException(500, f"failed to list {p}: {e}") from e

    # 按名字（不区分大小写）排序，目录浏览的最自然顺序
    entries.sort(key=lambda e: e["name"].lower())
    if len(entries) > limit:
        entries = entries[:limit]
        truncated = True
    else:
        truncated = False

    parent: Optional[str]
    if p.parent == p:  # 已到根 "/"
        parent = None
    else:
        parent = str(p.parent)

    return {
        "path": str(p),
        "parent": parent,
        "entries": entries,
        "truncated": truncated,
    }
