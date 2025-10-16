"""
Network management and diagnostics API endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, validator
from typing import Optional
import logging

from app.services.network_monitor import network_monitor
from app.deps.csrf import csrf_protection
from app.deps.admin_auth import admin_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/network", tags=["网络管理"])


# ==================== Data models ====================

class InterfaceToggleRequest(BaseModel):
    interface: str
    enable: bool

    @validator('interface')
    def validate_interface(cls, v):
        if not v or len(v) > 64:
            raise ValueError('无效的接口名称')
        # Basic safeguard to discourage command injection via the interface name.
        if not v.replace('-', '').replace('_', '').replace('.', '').isalnum():
            raise ValueError('接口名称包含非法字符')
        return v


class IPConfigRequest(BaseModel):
    interface: str
    ip_address: str
    netmask: str
    gateway: Optional[str] = None
    persistent: bool = False

    @validator('interface')
    def validate_interface(cls, v):
        if not v or len(v) > 64:
            raise ValueError('无效的接口名称')
        if not v.replace('-', '').replace('_', '').replace('.', '').isalnum():
            raise ValueError('接口名称包含非法字符')
        return v

    @validator('ip_address')
    def validate_ip(cls, v):
        if not v:
            raise ValueError('IP地址不能为空')
        return v

    @validator('netmask')
    def validate_netmask(cls, v):
        if not v:
            raise ValueError('子网掩码不能为空')
        return v


class PingRequest(BaseModel):
    target: str
    count: Optional[int] = 4

    @validator('target')
    def validate_target(cls, v):
        if not v or len(v) > 255:
            raise ValueError('无效的目标地址')
        # Basic sanitisation to filter unexpected characters.
        allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:')
        if not all(c in allowed_chars for c in v):
            raise ValueError('目标地址包含非法字符')
        return v

    @validator('count')
    def validate_count(cls, v):
        if v and (v < 1 or v > 20):
            raise ValueError('ping 次数必须在 1-20 之间')
        return v


# ==================== Route handlers ====================

@router.get("/interfaces")
async def get_interfaces():
    """
    Retrieve network interface information including address data and utilisation metrics.
    """
    try:
        interfaces = network_monitor.get_interfaces()
        return {
            "success": True,
            "interfaces": interfaces,
            "count": len(interfaces)
        }
    except Exception as e:
        logger.error(f"获取网络接口失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取网络接口失败", "error": str(e)}
        )


@router.get("/traffic-history")
async def get_traffic_history(
    window: str = Query(default="5min", regex="^(1min|5min|15min)$")
):
    """
    Provide historical traffic samples for the requested window (1min, 5min, 15min).
    """
    try:
        history = network_monitor.get_traffic_history(window)
        return {
            "success": True,
            **history
        }
    except Exception as e:
        logger.error(f"获取流量历史失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取流量历史失败", "error": str(e)}
        )


@router.get("/connections")
async def get_connections():
    """
    Return active network connections or aggregated statistics depending on privileges.
    """
    try:
        connections = network_monitor.get_connections()
        return connections
    except Exception as e:
        logger.error(f"获取连接信息失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取连接信息失败", "error": str(e)}
        )


@router.post(
    "/interface/toggle",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin)
    ]
)
async def toggle_interface(body: InterfaceToggleRequest):
    """
    Enable or disable a network interface. Requires admin privileges and a valid CSRF token.
    """
    logger.info(f"接口切换请求: {body.interface}, enable={body.enable}")

    result = network_monitor.execute_interface_toggle(
        body.interface,
        body.enable
    )

    if not result["success"]:
        raise HTTPException(
            status_code=500,
            detail=result
        )

    return result


@router.post(
    "/interface/config",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin)
    ]
)
async def config_interface(body: IPConfigRequest):
    """
    Apply static IP settings to an interface. Requires admin privileges and CSRF validation.
    """
    logger.info(
        f"IP配置请求: {body.interface}, "
        f"IP={body.ip_address}/{body.netmask}, "
        f"gateway={body.gateway}, "
        f"persistent={body.persistent}"
    )

    result = network_monitor.execute_ip_config(
        iface=body.interface,
        ip_addr=body.ip_address,
        netmask=body.netmask,
        gateway=body.gateway,
        persistent=body.persistent
    )

    if not result["success"]:
        raise HTTPException(
            status_code=500,
            detail=result
        )

    return result


@router.post(
    "/diagnostic/ping",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin)
    ]
)
async def diagnostic_ping(body: PingRequest):
    """
    Execute a ping diagnostic. Requires admin privileges and CSRF validation.
    """
    logger.info(f"Ping请求: target={body.target}, count={body.count}")

    result = network_monitor.execute_ping(
        target=body.target,
        count=body.count
    )

    return result


# ==================== Background sampling task ====================

@router.on_event("startup")
async def start_traffic_sampling():
    """
    Start a traffic sampling coroutine during application startup.
    """
    import asyncio

    async def sample_loop():
        """Continuously sample traffic metrics in the background."""
        while True:
            try:
                network_monitor.sample_traffic()
            except Exception as e:
                logger.error(f"流量采样失败: {e}")
            await asyncio.sleep(5)  # Sample every five seconds.

    # Launch the asynchronous sampling task.
    asyncio.create_task(sample_loop())
    logger.info("网络流量采样任务已启动(5秒间隔)")
