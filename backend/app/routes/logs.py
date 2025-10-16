"""
Journalctl log query endpoints.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.deps.admin_auth import admin_auth
from app.services.journalctl_reader import JournalctlError, journalctl_reader

router = APIRouter(prefix="/api/logs", tags=["系统日志"])


def _is_admin(request: Request) -> bool:
    admin_session_id = request.cookies.get("admin_session_id")
    if not admin_session_id:
        return False
    if admin_auth.validate_session(admin_session_id):
        return True
    admin_auth.cleanup_expired_sessions()
    return False


@router.get("/metadata")
async def get_metadata(request: Request):
    """Return available journal filters and default limits."""
    is_admin = _is_admin(request)
    accessible, message = journalctl_reader.probe_access(is_admin=is_admin)

    return {
        "priorities": journalctl_reader.priorities(),
        "limits": {
            "default": journalctl_reader.default_limit_admin
            if is_admin
            else journalctl_reader.default_limit_user,
            "max": journalctl_reader.max_limit_admin
            if is_admin
            else journalctl_reader.max_limit_user,
        },
        "is_admin": is_admin,
        "access": {
            "granted": accessible,
            "message": message,
        },
    }


@router.get("/query")
async def query_logs(
    request: Request,
    limit: Optional[int] = Query(default=None, ge=1, le=2000, description="返回条数"),
    cursor: Optional[str] = Query(default=None, description="游标，用于加载更多"),
    priority: Optional[str] = Query(default=None, description="优先级或范围，例如 info 或 3..6"),
    keyword: Optional[str] = Query(default=None, description="正则关键字"),
    since: Optional[str] = Query(default=None, description="起始时间 ISO8601"),
    until: Optional[str] = Query(default=None, description="结束时间 ISO8601"),
):
    """Proxy journalctl queries with role-aware limits and validation."""
    is_admin = _is_admin(request)

    try:
        result = journalctl_reader.query(
            is_admin=is_admin,
            limit=limit,
            cursor=cursor,
            since=since,
            until=until,
            priority=priority,
            keyword=keyword,
        )
    except JournalctlError as exc:
        status = 503
        if exc.code in {"INVALID_PRIORITY"}:
            status = 400
        elif exc.code == "JOURNALCTL_NOT_FOUND":
            status = 501
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return result
