"""
系统管理相关路由
"""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
import subprocess
import logging
import asyncio
from datetime import datetime, timedelta
from app.services.system_monitor import system_monitor
from app.deps.csrf import csrf_protection
from app.deps.admin_auth import admin_auth

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
    
    # 计算延迟执行时间 (5 秒后)
    scheduled_time = datetime.utcnow() + timedelta(seconds=5)
    scheduled_time_str = scheduled_time.isoformat() + "Z"
    
    # 准备系统命令
    if action == "restart":
        command = ["sudo", "systemctl", "reboot"]
        message = "系统将在 5 秒后重启"
    else:  # shutdown
        command = ["sudo", "systemctl", "poweroff"]
        message = "系统将在 5 秒后关机"
    
    # 异步执行(延迟 5 秒)
    async def delayed_power_action():
        await asyncio.sleep(5)
        try:
            logger.warning(f"执行电源操作: {action}")
            subprocess.run(command, check=True, timeout=10)
        except subprocess.CalledProcessError as e:
            logger.error(f"电源操作失败: {e}")
        except Exception as e:
            logger.error(f"电源操作异常: {e}")
    
    # 启动后台任务
    asyncio.create_task(delayed_power_action())
    
    logger.warning(f"已安排电源操作: {action}, 将在 {scheduled_time_str} 执行")
    
    return PowerResponse(
        success=True,
        message=message,
        scheduled_time=scheduled_time_str
    )