"""
文件传输相关路由
"""
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    Request,
    Response,
    Query,
)
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from app.deps.csrf import csrf_protection
from app.deps.admin_auth import admin_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["文件传输"])

# ==================== 常量 ====================
DEFAULT_USER_ROOT = Path.home()
ADMIN_ROOT = Path("/")
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB


# ==================== 工具函数 ====================

def _is_admin(request: Request) -> bool:
    """
    根据 admin_session_id Cookie 判断是否为管理员模式
    """
    admin_session_id = request.cookies.get("admin_session_id")
    if not admin_session_id:
        return False

    if admin_auth.validate_session(admin_session_id):
        return True

    # 如果 session 已失效则顺带清理
    admin_auth.cleanup_expired_sessions()
    return False


def _get_root(request: Request) -> Path:
    return ADMIN_ROOT if _is_admin(request) else DEFAULT_USER_ROOT


def _normalize_path(
    request: Request,
    target: Optional[str],
    expect_directory: bool = False,
) -> Path:
    """
    将客户端传入的路径转换为服务器端路径，并校验越权访问
    """
    root = _get_root(request)
    is_admin = root == ADMIN_ROOT

    if not target:
        resolved = root
    else:
        candidate = Path(target)
        if candidate.is_absolute():
            if not is_admin:
                raise HTTPException(
                    status_code=403,
                    detail={"message": "当前模式不允许访问绝对路径"},
                )
            resolved = candidate.resolve()
        else:
            resolved = (root / candidate).resolve()

    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail={"message": "访问的路径超出允许范围"},
        )

    if expect_directory and not resolved.is_dir():
        raise HTTPException(
            status_code=400,
            detail={"message": "目标目录不存在或不是目录"},
        )

    return resolved


def _client_path(path: Path, root: Path) -> str:
    """
    转换为前端可使用的路径
    """
    if root == ADMIN_ROOT:
        return str(path)
    try:
        rel = path.relative_to(root)
        return "" if str(rel) == "." else str(rel)
    except ValueError:
        # 保险，如果越界，返回空字符串
        return ""


def _build_entry(item: Path, root: Path) -> Dict[str, Any]:
    """
    构造目录项信息
    """
    try:
        stat_result = item.stat()
        size = stat_result.st_size
        modified = stat_result.st_mtime
    except (FileNotFoundError, PermissionError, OSError):
        size = 0
        modified = 0

    entry: Dict[str, Any] = {
        "name": item.name,
        "is_dir": item.is_dir(),
        "is_symlink": item.is_symlink(),
        "size": size,
        "modified": modified,
        "path": _client_path(item, root),
        "absolute_path": str(item),
    }

    return entry


def _build_breadcrumbs(current: Path, root: Path) -> List[Dict[str, str]]:
    breadcrumbs: List[Dict[str, str]] = []
    if current == root:
        breadcrumbs.append({"name": root.name or "/", "path": ""})
        return breadcrumbs

    try:
        relative = current.relative_to(root)
    except ValueError:
        # 越界时直接返回完整路径
        breadcrumbs.append({"name": str(current), "path": str(current)})
        return breadcrumbs

    breadcrumbs.append({"name": root.name or "/", "path": ""})
    parts = list(relative.parts)
    cumulative = Path()
    for part in parts:
        cumulative /= part
        breadcrumbs.append({"name": part, "path": str(cumulative)})
    return breadcrumbs


# ==================== 路由 ====================

@router.get("/list")
async def list_directory(
    request: Request,
    path: Optional[str] = Query(default=None, description="要浏览的路径"),
):
    """
    列出目录内容
    """
    try:
        root = _get_root(request)
        current = _normalize_path(request, path, expect_directory=True)

        entries: List[Dict[str, Any]] = []
        try:
            for item in sorted(current.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                entries.append(_build_entry(item, root))
        except PermissionError:
            raise HTTPException(
                status_code=403,
                detail={"message": "没有权限读取该目录"},
            )

        return {
            "root_path": str(root),
            "current_path": _client_path(current, root),
            "absolute_path": str(current),
            "is_admin": root == ADMIN_ROOT,
            "entries": entries,
            "breadcrumbs": _build_breadcrumbs(current, root),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("列出目录失败: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"message": "列出目录失败", "error": str(e)},
        )


@router.post(
    "/upload",
    dependencies=[
        Depends(csrf_protection.validate_token),
    ],
)
async def upload_file(
    request: Request,
    response: Response,
    directory: Optional[str] = Query(default=None, description="上传目标目录"),
    file: UploadFile = File(...),
):
    """
    上传文件到指定目录
    """
    target_dir = _normalize_path(request, directory or "", expect_directory=True)
    destination = target_dir / file.filename

    if destination.exists():
        raise HTTPException(
            status_code=409,
            detail={"message": "目标文件已存在，禁止覆盖"},
        )

    total_written = 0
    try:
        with destination.open("wb") as out_file:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > MAX_UPLOAD_SIZE:
                    destination.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "message": "文件过大，超过限制",
                            "limit_bytes": MAX_UPLOAD_SIZE,
                        },
                    )
                out_file.write(chunk)
    except HTTPException:
        raise
    except PermissionError:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=403,
            detail={"message": "没有权限写入指定目录"},
        )
    except Exception as e:
        destination.unlink(missing_ok=True)
        logger.error("上传文件失败: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"message": "上传文件失败", "error": str(e)},
        )

    logger.info("文件上传成功: %s -> %s", file.filename, destination)
    response.status_code = 201
    return {
        "message": "上传成功",
        "filename": file.filename,
        "size": total_written,
        "path": _client_path(destination, _get_root(request)),
    }


@router.get("/download")
async def download_file(
    request: Request,
    path: str = Query(..., description="要下载的文件路径"),
):
    """
    下载指定文件
    """
    file_path = _normalize_path(request, path)

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"message": "文件不存在"},
        )

    try:
        return FileResponse(
            path=file_path,
            media_type="application/octet-stream",
            filename=file_path.name,
        )
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail={"message": "没有权限读取该文件"},
        )


@router.delete(
    "",
    dependencies=[
        Depends(csrf_protection.validate_token),
    ],
)
async def delete_file(
    request: Request,
    path: str = Query(..., description="要删除的文件路径"),
):
    """
    删除指定文件
    """
    file_path = _normalize_path(request, path)

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail={"message": "文件不存在"},
        )

    if file_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail={"message": "暂不支持删除文件夹"},
        )

    try:
        file_path.unlink()
    except PermissionError:
        raise HTTPException(
            status_code=403,
            detail={"message": "没有权限删除该文件"},
        )
    except Exception as e:
        logger.error("删除文件失败: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"message": "删除文件失败", "error": str(e)},
        )

    logger.info("删除文件成功: %s", file_path)
    return {"message": "删除成功", "path": _client_path(file_path, _get_root(request))}
