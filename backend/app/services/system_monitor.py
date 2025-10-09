"""
系统监控服务
提供 CPU、内存、磁盘、网络等系统信息
"""
import psutil
import time
import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class SystemMonitor:
    """系统监控服务"""
    
    def __init__(self):
        self._cache = {}
        self._cache_duration = 2  # 缓存 2 秒
        self._last_net_io = None
        self._last_net_time = None
    
    def _is_cache_valid(self, key: str) -> bool:
        """检查缓存是否有效"""
        if key not in self._cache:
            return False
        cache_time, _ = self._cache[key]
        return (time.time() - cache_time) < self._cache_duration
    
    def _get_cached(self, key: str) -> Any:
        """获取缓存数据"""
        if self._is_cache_valid(key):
            _, data = self._cache[key]
            return data
        return None
    
    def _set_cache(self, key: str, data: Any):
        """设置缓存"""
        self._cache[key] = (time.time(), data)
    
    def get_uptime(self) -> int:
        """获取系统运行时长(秒)"""
        try:
            return int(time.time() - psutil.boot_time())
        except Exception as e:
            logger.error(f"获取系统运行时长失败: {e}")
            return 0
    
    def get_disk_info(self) -> Dict[str, Any]:
        """获取磁盘信息"""
        cached = self._get_cached('disk')
        if cached:
            return cached
        
        try:
            disk = psutil.disk_usage('/')
            data = {
                "usage_percent": round(disk.percent, 1),
                "used_gb": round(disk.used / (1024**3), 1),
                "total_gb": round(disk.total / (1024**3), 1)
            }
            self._set_cache('disk', data)
            return data
        except Exception as e:
            logger.error(f"获取磁盘信息失败: {e}")
            return {"usage_percent": 0, "used_gb": 0, "total_gb": 0}
    
    def get_memory_info(self) -> Dict[str, Any]:
        """获取内存信息"""
        cached = self._get_cached('memory')
        if cached:
            return cached
        
        try:
            mem = psutil.virtual_memory()
            data = {
                "usage_percent": round(mem.percent, 1),
                "used_gb": round(mem.used / (1024**3), 2),
                "total_gb": round(mem.total / (1024**3), 2)
            }
            self._set_cache('memory', data)
            return data
        except Exception as e:
            logger.error(f"获取内存信息失败: {e}")
            return {"usage_percent": 0, "used_gb": 0, "total_gb": 0}
    
    def get_cpu_info(self) -> Dict[str, Any]:
        """获取 CPU 信息"""
        cached = self._get_cached('cpu')
        if cached:
            return cached
        
        try:
            # 缩短采样窗口，降低接口首次调用延迟
            cpu_percent = psutil.cpu_percent(interval=0.1)
            cpu_count = psutil.cpu_count()
            
            data = {
                "usage_percent": round(cpu_percent, 1),
                "cores": cpu_count
            }
            self._set_cache('cpu', data)
            return data
        except Exception as e:
            logger.error(f"获取 CPU 信息失败: {e}")
            return {"usage_percent": 0, "cores": 0}
    
    def get_network_speed(self) -> Dict[str, Any]:
        """获取网络速度 (Mbps)"""
        try:
            current_time = time.time()
            current_io = psutil.net_io_counters()
            
            # 首次调用,无法计算速率
            if self._last_net_io is None or self._last_net_time is None:
                self._last_net_io = current_io
                self._last_net_time = current_time
                return {
                    "speed_mbps": 0.0,
                    "upload_mbps": 0.0,
                    "download_mbps": 0.0
                }
            
            # 计算时间差
            time_delta = current_time - self._last_net_time
            if time_delta < 0.1:  # 时间间隔太短
                return {
                    "speed_mbps": 0.0,
                    "upload_mbps": 0.0,
                    "download_mbps": 0.0
                }
            
            # 计算流量差 (字节)
            bytes_sent = current_io.bytes_sent - self._last_net_io.bytes_sent
            bytes_recv = current_io.bytes_recv - self._last_net_io.bytes_recv
            
            # 转换为 Mbps (1 Mbps = 1,000,000 bits/s = 125,000 bytes/s)
            upload_mbps = round((bytes_sent / time_delta) / 125000, 1)
            download_mbps = round((bytes_recv / time_delta) / 125000, 1)
            speed_mbps = round(upload_mbps + download_mbps, 1)
            
            # 更新状态
            self._last_net_io = current_io
            self._last_net_time = current_time
            
            return {
                "speed_mbps": speed_mbps,
                "upload_mbps": upload_mbps,
                "download_mbps": download_mbps
            }
        except Exception as e:
            logger.error(f"获取网络速度失败: {e}")
            return {
                "speed_mbps": 0.0,
                "upload_mbps": 0.0,
                "download_mbps": 0.0
            }
    
    def get_full_status(self) -> Dict[str, Any]:
        """获取完整的系统状态"""
        return {
            "uptime_seconds": self.get_uptime(),
            "disk": self.get_disk_info(),
            "memory": self.get_memory_info(),
            "cpu": self.get_cpu_info(),
            "network": self.get_network_speed()
        }


# 全局实例
system_monitor = SystemMonitor()
