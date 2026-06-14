"""Writing API —— Markdown 写作页的安全文件读写端点。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api/writing")

_ALLOWED_SUFFIXES = {".md", ".markdown", ".txt"}
_SENSITIVE_DIRS = tuple(
    Path(p).resolve()
    for p in (
        "/System", "/Library", "/usr", "/etc", "/bin", "/sbin", "/var",
        "/private/etc", "/private/var",
    )
    if Path(p).exists()
)


class WriteWritingFileReq(BaseModel):
    root: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    content: str = ""


class DeleteWritingFileReq(BaseModel):
    root: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)


class CreateWritingFileReq(BaseModel):
    root: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    content: str = ""


def _abs(raw: str) -> Path:
    raw = (raw or "").strip()
    if not raw:
        raise HTTPException(400, "path is required")
    return Path(os.path.expandvars(raw)).expanduser().resolve(strict=False)


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_root(raw_root: str) -> Path:
    root = _abs(raw_root)
    if not root.exists():
        raise HTTPException(404, f"root not found: {root}")
    if not root.is_dir():
        raise HTTPException(400, f"root is not a directory: {root}")
    if root.parent == root:
        raise HTTPException(400, "root directory is not allowed")
    if any(root == p or _is_relative_to(root, p) for p in _SENSITIVE_DIRS):
        raise HTTPException(403, f"sensitive root is not allowed: {root}")
    if not os.access(root, os.R_OK | os.X_OK):
        raise HTTPException(403, f"permission denied: {root}")
    return root


def _validate_file_path(raw_path: str, root: Path, *, must_exist: bool) -> Path:
    path = _abs(raw_path)
    if not _is_relative_to(path, root):
        raise HTTPException(403, "file is outside writing root")
    if path.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise HTTPException(400, "only .md, .markdown and .txt files are allowed")
    if must_exist:
        if not path.exists():
            raise HTTPException(404, f"file not found: {path}")
        if not path.is_file():
            raise HTTPException(400, f"not a file: {path}")
        if not os.access(path, os.R_OK):
            raise HTTPException(403, f"permission denied: {path}")
    return path


def _safe_create_path(root: Path, name: str) -> Path:
    clean = Path(name.strip())
    if clean.name != name.strip() or clean.name in {"", ".", ".."}:
        raise HTTPException(400, "file name must not contain path separators")
    path = (root / clean.name).resolve(strict=False)
    return _validate_file_path(str(path), root, must_exist=False)


def _file_meta(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "size": stat.st_size,
        "updated_at": stat.st_mtime,
    }


@router.get("/list")
def list_writing_files(
    path: str = Query(..., description="写作空间目录"),
    limit: int = 200,
    max_scan: int = 1000,
):
    if limit < 1 or limit > 1000:
        raise HTTPException(400, "limit must be between 1 and 1000")
    if max_scan < 1 or max_scan > 10000:
        raise HTTPException(400, "max_scan must be between 1 and 10000")
    root = _validate_root(path)
    files: list[dict] = []
    truncated = False
    scanned = 0
    try:
        with os.scandir(root) as it:
            for entry in it:
                if scanned >= max_scan:
                    truncated = True
                    break
                scanned += 1
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                fp = (root / entry.name).resolve(strict=False)
                if fp.suffix.lower() not in _ALLOWED_SUFFIXES:
                    continue
                if len(files) >= limit:
                    truncated = True
                    break
                files.append(_file_meta(fp))
    except PermissionError:
        raise HTTPException(403, f"permission denied while listing: {root}") from None
    except OSError as e:
        raise HTTPException(500, f"failed to list {root}: {e}") from e

    files.sort(key=lambda item: item["name"].lower())
    return {"root": str(root), "files": files, "truncated": truncated}


@router.get("/read")
def read_writing_file(
    path: str = Query(..., description="文件绝对路径"),
    root: str = Query(..., description="写作空间目录"),
):
    root_path = _validate_root(root)
    file_path = _validate_file_path(path, root_path, must_exist=True)
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "file is not valid UTF-8 text") from None
    except OSError as e:
        raise HTTPException(500, f"failed to read {file_path}: {e}") from e
    return {"root": str(root_path), "name": file_path.name, "path": str(file_path), "content": content}


@router.post("/write")
def write_writing_file(req: WriteWritingFileReq):
    root = _validate_root(req.root)
    file_path = _validate_file_path(req.path, root, must_exist=True)
    if not os.access(file_path, os.W_OK):
        raise HTTPException(403, f"permission denied: {file_path}")
    try:
        file_path.write_text(req.content, encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"failed to write {file_path}: {e}") from e
    return {"ok": True, "path": str(file_path), "bytes": len(req.content.encode("utf-8"))}


@router.post("/create")
def create_writing_file(req: CreateWritingFileReq):
    root = _validate_root(req.root)
    file_path = _safe_create_path(root, req.name)
    if file_path.exists():
        raise HTTPException(409, f"file already exists: {file_path.name}")
    if not os.access(root, os.W_OK):
        raise HTTPException(403, f"permission denied: {root}")
    try:
        file_path.write_text(req.content, encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"failed to create {file_path}: {e}") from e
    return {"ok": True, **_file_meta(file_path)}


@router.post("/delete")
def delete_writing_file(req: DeleteWritingFileReq):
    root = _validate_root(req.root)
    file_path = _validate_file_path(req.path, root, must_exist=True)
    if not os.access(file_path, os.W_OK):
        raise HTTPException(403, f"permission denied: {file_path}")
    try:
        file_path.unlink()
    except OSError as e:
        raise HTTPException(500, f"failed to delete {file_path}: {e}") from e
    return {"ok": True, "path": str(file_path)}
