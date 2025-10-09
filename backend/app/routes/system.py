"""
系统管理相关路由
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
import logging
from datetime import datetime
from app.services.system_monitor import system_monitor
from app.deps.csrf import csrf_protection
from app.deps.admin_auth import admin_auth
from app.services.admin_privileged import execute_power_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["系统管理"])


# ==================== 数据模型 ====================

class PowerRequest(BaseModel):
    action: str  # "restart" 或 "shutdown"


class PowerResponse(BaseModel):
    success: bool
    message: str
    scheduled_time: str


# ==================== 路由处理器 ====================

@router.get("/status")
async def get_system_status():
    """
    获取系统状态
    
    返回: CPU、内存、磁盘、网络等实时信息
    """
    try:
        status = system_monitor.get_full_status()
        return status
    except Exception as e:
        logger.error(f"获取系统状态失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取系统状态失败", "error": str(e)}
        )


@router.post(
    "/power",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin)
    ]
)
async def power_control(body: PowerRequest, response: Response):
    """
    电源管理 (重启/关机)
    
    需要管理员权限
    需要 CSRF Token
    """
    action = body.action.lower()
    
    if action not in ["restart", "shutdown"]:
        raise HTTPException(
            status_code=400,
            detail={"message": "无效的操作,仅支持 restart 或 shutdown"}
        )
    
    if action == "restart":
        message = "系统即将重启"
        helper_action = "reboot"
    else:
        message = "系统即将关机"
        helper_action = "shutdown"

    logger.warning(f"执行电源操作: {action}")

    if not execute_power_action(helper_action):
        logger.error(f"电源操作触发失败: {action}")
        raise HTTPException(
            status_code=500,
            detail={"message": "电源操作执行失败", "action": action}
        )

    scheduled_time = datetime.utcnow()
    scheduled_time_str = scheduled_time.isoformat() + "Z"

    return PowerResponse(
        success=True,
        message=message,
        scheduled_time=scheduled_time_str
    )
