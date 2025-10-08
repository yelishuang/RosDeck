"""
设备信息相关路由
"""
from fastapi import APIRouter, HTTPException
import socket
import platform
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/device", tags=["设备信息"])


def get_local_ip() -> str:
    """
    获取局域网 IP 地址(非 127.0.0.1)
    """
    try:
        # 创建一个 UDP socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 连接到外部地址(不实际发送数据)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception as e:
        logger.warning(f"无法获取局域网 IP: {e}")
        return "127.0.0.1"


@router.get("/info")
async def get_device_info():
    """
    获取设备基本信息
    
    返回:
        - hostname: 主机名
        - status: 在线状态
        - os: 操作系统
        - architecture: 架构
        - ip_address: IP 地址
    """
    try:
        hostname = socket.gethostname()
        os_info = f"{platform.system()} {platform.release()}"
        
        # 尝试识别 openEuler
        try:
            with open("/etc/os-release", "r") as f:
                content = f.read()
                if "openEuler" in content:
                    # 提取版本号
                    for line in content.split('\n'):
                        if line.startswith("VERSION="):
                            version = line.split('=')[1].strip('"')
                            os_info = f"openEuler {version}"
                            break
        except:
            pass  # 如果读取失败,使用默认值
        
        architecture = platform.machine()
        ip_address = get_local_ip()
        
        return {
            "hostname": hostname,
            "status": "online",
            "os": os_info,
            "architecture": architecture,
            "ip_address": ip_address
        }
    except Exception as e:
        logger.error(f"获取设备信息失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取设备信息失败", "error": str(e)}
        )