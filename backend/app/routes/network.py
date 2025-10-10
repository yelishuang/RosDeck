"""
网络管理相关路由
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


# ==================== 数据模型 ====================

class InterfaceToggleRequest(BaseModel):
    interface: str
    enable: bool

    @validator('interface')
    def validate_interface(cls, v):
        if not v or len(v) > 64:
            raise ValueError('无效的接口名称')
        # 简单的安全检查，防止命令注入
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
        # 简单的安全检查
        allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:')
        if not all(c in allowed_chars for c in v):
            raise ValueError('目标地址包含非法字符')
        return v

    @validator('count')
    def validate_count(cls, v):
        if v and (v < 1 or v > 20):
            raise ValueError('ping 次数必须在 1-20 之间')
        return v


# ==================== 路由处理器 ====================

@router.get("/interfaces")
async def get_interfaces():
    """
    获取所有网络接口信息

    返回: 接口列表(名称、IP、MAC、状态、流量统计)
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
    获取流量历史数据

    Args:
        window: 时间窗口 ("1min", "5min", "15min")

    返回: 包含时间戳和上传/下载速率的数据点列表
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
    获取网络连接信息

    返回: 连接列表或汇总统计(取决于权限)
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
    启用/禁用网络接口

    需要管理员权限
    需要 CSRF Token
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
    配置静态IP地址

    需要管理员权限
    需要 CSRF Token
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
    执行 ping 诊断

    需要管理员权限
    需要 CSRF Token
    """
    logger.info(f"Ping请求: target={body.target}, count={body.count}")

    result = network_monitor.execute_ping(
        target=body.target,
        count=body.count
    )

    return result


# ==================== 后台采样任务 ====================

@router.on_event("startup")
async def start_traffic_sampling():
    """
    应用启动时启动流量采样任务
    注意：这是一个简化实现，实际应使用 BackgroundTasks 或 asyncio.create_task
    """
    import asyncio

    async def sample_loop():
        """后台采样循环"""
        while True:
            try:
                network_monitor.sample_traffic()
            except Exception as e:
                logger.error(f"流量采样失败: {e}")
            await asyncio.sleep(5)  # 每5秒采样一次

    # 启动后台任务
    asyncio.create_task(sample_loop())
    logger.info("网络流量采样任务已启动(5秒间隔)")
