"""
System monitoring utilities providing CPU, memory, disk, and network metrics.
"""
import psutil
import time
import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class SystemMonitor:
    """Collects and caches host-level utilisation metrics."""
    
    def __init__(self):
        self._cache = {}
        self._cache_duration = 2  # Cache entries for two seconds.
        self._last_net_io = None
        self._last_net_time = None
    
    def _is_cache_valid(self, key: str) -> bool:
        """Return True when the cached entry is still fresh."""
        if key not in self._cache:
            return False
        cache_time, _ = self._cache[key]
        return (time.time() - cache_time) < self._cache_duration
    
    def _get_cached(self, key: str) -> Any:
        """Fetch a cached value when available."""
        if self._is_cache_valid(key):
            _, data = self._cache[key]
            return data
        return None
    
    def _set_cache(self, key: str, data: Any):
        """Persist a value in the in-memory cache."""
        self._cache[key] = (time.time(), data)
    
    def get_uptime(self) -> int:
        """Return the system uptime in seconds."""
        try:
            return int(time.time() - psutil.boot_time())
        except Exception as e:
            logger.error(f"获取系统运行时长失败: {e}")
            return 0
    
    def get_disk_info(self) -> Dict[str, Any]:
        """Summarise disk utilisation for the root filesystem."""
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
        """Summarise memory utilisation."""
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
        """Return aggregated CPU utilisation."""
        cached = self._get_cached('cpu')
        if cached:
            return cached
        
        try:
            # Use a short sampling window to minimise initial latency.
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
        """Estimate network throughput in Mbps."""
        try:
            current_time = time.time()
            current_io = psutil.net_io_counters()
            
            # Without a previous sample we cannot calculate throughput.
            if self._last_net_io is None or self._last_net_time is None:
                self._last_net_io = current_io
                self._last_net_time = current_time
                return {
                    "speed_mbps": 0.0,
                    "upload_mbps": 0.0,
                    "download_mbps": 0.0
                }
            
            # Compute elapsed time.
            time_delta = current_time - self._last_net_time
            if time_delta < 0.1:  # Ignore windows that are too short for accuracy.
                return {
                    "speed_mbps": 0.0,
                    "upload_mbps": 0.0,
                    "download_mbps": 0.0
                }
            
            # Calculate byte deltas.
            bytes_sent = current_io.bytes_sent - self._last_net_io.bytes_sent
            bytes_recv = current_io.bytes_recv - self._last_net_io.bytes_recv
            
            # Convert bytes per second to Mbps (125,000 bytes/s).
            upload_mbps = round((bytes_sent / time_delta) / 125000, 1)
            download_mbps = round((bytes_recv / time_delta) / 125000, 1)
            speed_mbps = round(upload_mbps + download_mbps, 1)
            
            # Update sample state.
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
        """Aggregate all monitored subsystems into a single payload."""
        return {
            "uptime_seconds": self.get_uptime(),
            "disk": self.get_disk_info(),
            "memory": self.get_memory_info(),
            "cpu": self.get_cpu_info(),
            "network": self.get_network_speed()
        }


# Singleton instance used across the application.
system_monitor = SystemMonitor()
