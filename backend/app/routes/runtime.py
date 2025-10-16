"""
Runtime API routes for process and service management.
"""

from fastapi import APIRouter, HTTPException, Cookie, Header
from typing import Optional
from pydantic import BaseModel
import logging

from ..services.process_monitor import ProcessMonitor
from ..services.service_manager import ServiceManager
from ..deps.admin_auth import admin_auth
from ..deps.csrf import csrf_protection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/runtime", tags=["运行中心"])


# Helper: validate CSRF tokens against the server-side value.
def validate_csrf_token(token: Optional[str]) -> bool:
    """Return True when the provided token matches the stored CSRF token."""
    expected_token = csrf_protection.get_token()
    return token is not None and token == expected_token


# Request payload models.
class KillProcessRequest(BaseModel):
    pid: int


class ServiceActionRequest(BaseModel):
    service_name: str
    action: str  # Supported actions: start, stop, restart, enable, disable.


# Route handlers.
@router.get("/processes")
async def get_processes(sort_by: str = "cpu", limit: int = 500):
    """
    Get list of running processes.
    Available to all authenticated users (read-only).
    """
    try:
        processes = ProcessMonitor.get_processes(sort_by=sort_by, limit=limit)
        return {
            "success": True,
            "processes": processes,
            "count": len(processes)
        }
    except Exception as e:
        logger.error(f"Error getting processes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/processes/kill")
async def kill_process(
    request: KillProcessRequest,
    admin_session_id: Optional[str] = Cookie(None),
    x_csrf_token: Optional[str] = Header(None)
):
    """
    Terminate a process by PID.
    Requires admin mode and CSRF token.
    """
    # Ensure the caller holds a valid admin session.
    if not admin_session_id or not admin_auth.validate_session(admin_session_id):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    # Enforce CSRF token verification.
    if not validate_csrf_token(x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    try:
        success, message = ProcessMonitor.kill_process(request.pid)

        if success:
            return {"success": True, "message": message}
        else:
            raise HTTPException(status_code=400, detail=message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error killing process: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/services")
async def get_services():
    """
    Get list of systemd services.
    Available to all authenticated users (read-only).
    """
    try:
        services = ServiceManager.get_services()
        return {
            "success": True,
            "services": services,
            "count": len(services)
        }
    except Exception as e:
        logger.error(f"Error getting services: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/services/action")
async def service_action(
    request: ServiceActionRequest,
    admin_session_id: Optional[str] = Cookie(None),
    x_csrf_token: Optional[str] = Header(None)
):
    """
    Perform action on a systemd service.
    Requires admin mode and CSRF token.
    Actions: start, stop, restart, enable, disable
    """
    # Ensure the caller holds a valid admin session.
    if not admin_session_id or not admin_auth.validate_session(admin_session_id):
        raise HTTPException(status_code=403, detail="Admin privileges required")

    # Enforce CSRF token verification.
    if not validate_csrf_token(x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    try:
        success, message = ServiceManager.control_service(
            request.service_name,
            request.action
        )

        if success:
            return {"success": True, "message": message}
        else:
            raise HTTPException(status_code=400, detail=message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error performing service action: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/services/{service_name}/status")
async def get_service_status(service_name: str):
    """
    Get detailed status of a specific service.
    Available to all authenticated users.
    """
    try:
        status = ServiceManager.get_service_status(service_name)

        if status:
            return {"success": True, "status": status}
        else:
            raise HTTPException(status_code=404, detail="Service not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
