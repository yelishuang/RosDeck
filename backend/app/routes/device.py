"""
Device metadata endpoints.
"""
from fastapi import APIRouter, HTTPException
import socket
import platform
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/device", tags=["设备信息"])


def get_local_ip() -> str:
    """
    Derive the LAN-facing IP address (excluding 127.0.0.1).
    """
    try:
        # Create a UDP socket for interface discovery.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Connect to an external endpoint without sending packets.
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
    Return hostname, OS metadata, architecture, and LAN IP address.
    """
    try:
        hostname = socket.gethostname()
        os_info = f"{platform.system()} {platform.release()}"
        
        # Attempt to detect openEuler builds from release metadata.
        try:
            with open("/etc/os-release", "r") as f:
                content = f.read()
                if "openEuler" in content:
                    # Infer the distribution version.
                    for line in content.split('\n'):
                        if line.startswith("VERSION="):
                            version = line.split('=')[1].strip('"')
                            os_info = f"openEuler {version}"
                            break
        except:
            pass  # Fall back to the default OS string when parsing fails.
        
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
