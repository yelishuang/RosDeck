"""
存储监控服务
负责收集磁盘总体信息、分区详情与历史趋势数据
"""
import os
import psutil
import time
import threading
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """返回当前 UTC ISO 时间字符串"""
    return datetime.now(timezone.utc).isoformat()


def _device_name(path: str) -> str:
    """提取设备名, 供映射 psutil disk io 数据使用"""
    return os.path.basename(path)


class StorageMonitor:
    """磁盘存储监控"""

    HISTORY_MAX_MINUTES = 10          # 历史记录保留时长
    SAMPLE_INTERVAL_SECONDS = 5       # 最小采样间隔

    def __init__(self):
        self._lock = threading.Lock()
        self._history: Deque[Dict[str, float]] = deque()
        self._last_sample_time: float = 0.0
        self._last_summary: Optional[Dict[str, object]] = None
        self._last_io: Optional[object] = None
        self._last_io_time: Optional[float] = None
        self._last_perdisk_io: Dict[str, Tuple[int, int, float]] = {}
        self._perdisk_rates: Dict[str, Dict[str, float]] = {}

    def _ensure_sample(self) -> Dict[str, object]:
        """确保在合理时间范围内生成最新采样"""
        with self._lock:
            current_time = time.time()

            if (
                self._last_summary is not None
                and (current_time - self._last_sample_time) < self.SAMPLE_INTERVAL_SECONDS
            ):
                return self._last_summary

            return self._collect_sample_locked(current_time)

    def _collect_sample_locked(self, current_time: float) -> Dict[str, object]:
        """在持有锁的情况下采样系统信息"""
        usage = psutil.disk_usage("/")
        io_counters = psutil.disk_io_counters()
        perdisk_counters = psutil.disk_io_counters(perdisk=True) or {}

        read_bytes_per_sec = 0.0
        write_bytes_per_sec = 0.0

        if self._last_io is not None and self._last_io_time:
            delta_time = max(current_time - self._last_io_time, 1e-6)
            read_bytes_per_sec = (io_counters.read_bytes - self._last_io.read_bytes) / delta_time
            write_bytes_per_sec = (io_counters.write_bytes - self._last_io.write_bytes) / delta_time

        read_bytes_per_sec = max(read_bytes_per_sec, 0.0)
        write_bytes_per_sec = max(write_bytes_per_sec, 0.0)

        # 计算各磁盘速率
        new_perdisk_rates: Dict[str, Dict[str, float]] = {}
        for device, stats in perdisk_counters.items():
            last = self._last_perdisk_io.get(device)
            if last:
                last_read, last_write, last_time = last
                delta_time = max(current_time - last_time, 1e-6)
                read_rate = max((stats.read_bytes - last_read) / delta_time, 0.0)
                write_rate = max((stats.write_bytes - last_write) / delta_time, 0.0)
            else:
                read_rate = 0.0
                write_rate = 0.0

            new_perdisk_rates[device] = {
                "read_bytes_per_sec": read_rate,
                "write_bytes_per_sec": write_rate,
            }
            self._last_perdisk_io[device] = (stats.read_bytes, stats.write_bytes, current_time)

        self._perdisk_rates = new_perdisk_rates
        self._last_io = io_counters
        self._last_io_time = current_time
        self._last_sample_time = current_time

        summary = {
            "timestamp": _now_iso(),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "usage_percent": round(usage.percent, 2),
            "io": {
                "read_bytes_per_sec": round(read_bytes_per_sec, 2),
                "write_bytes_per_sec": round(write_bytes_per_sec, 2),
            },
        }

        self._append_history_locked(summary["timestamp"], summary["usage_percent"], summary["io"])
        self._last_summary = summary
        return summary

    def _append_history_locked(self, timestamp: str, usage_percent: float, io_data: Dict[str, float]):
        """维护固定窗口历史记录"""
        self._history.append({
            "timestamp": timestamp,
            "usage_percent": usage_percent,
            "read_bytes_per_sec": round(io_data["read_bytes_per_sec"], 2),
            "write_bytes_per_sec": round(io_data["write_bytes_per_sec"], 2),
        })

        # 控制历史长度: SAMPLE_INTERVAL_SECONDS * 保留分钟
        max_len = int(self.HISTORY_MAX_MINUTES * 60 / max(self.SAMPLE_INTERVAL_SECONDS, 1))
        while len(self._history) > max_len:
            self._history.popleft()

    def get_summary(self) -> Dict[str, object]:
        """返回磁盘使用总览及历史"""
        summary = self._ensure_sample()

        with self._lock:
            history_snapshot = list(self._history)

        return {
            **summary,
            "history": history_snapshot,
        }

    def _partition_io_rates(self, device_path: str) -> Dict[str, float]:
        """根据设备路径返回对应 IO 速率"""
        name = _device_name(device_path)
        candidates = []
        trimmed = name
        while trimmed:
            candidates.append(trimmed)
            if not trimmed[-1].isdigit():
                break
            trimmed = trimmed[:-1]

        if trimmed.endswith("p"):
            candidates.append(trimmed[:-1])

        seen = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            rates = self._perdisk_rates.get(candidate)
            if rates:
                return {
                    "read_bytes_per_sec": round(rates["read_bytes_per_sec"], 2),
                    "write_bytes_per_sec": round(rates["write_bytes_per_sec"], 2),
                }

        return {"read_bytes_per_sec": 0.0, "write_bytes_per_sec": 0.0}

    def get_partitions(self) -> List[Dict[str, object]]:
        """列出所有有效分区信息"""
        self._ensure_sample()

        partitions = []
        for part in psutil.disk_partitions(all=False):
            # 排除虚拟/只读文件系统
            if not part.mountpoint or part.fstype in {"squashfs"}:
                continue

            try:
                usage = psutil.disk_usage(part.mountpoint)
            except PermissionError:
                logger.debug("无法访问挂载点 %s, 跳过", part.mountpoint)
                continue
            except FileNotFoundError:
                continue

            io_rates = self._partition_io_rates(part.device)

            partitions.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype or "unknown",
                "opts": part.opts,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "used_percent": round(usage.percent, 2),
                "read_bytes_per_sec": io_rates["read_bytes_per_sec"],
                "write_bytes_per_sec": io_rates["write_bytes_per_sec"],
                "warning": usage.percent >= 85.0,
            })

        partitions.sort(key=lambda item: item["mountpoint"])
        return partitions

    def build_report_payload(self) -> Dict[str, object]:
        """构造报表数据, 供 JSON/CSV 导出"""
        summary = self.get_summary()
        partitions = self.get_partitions()

        return {
            "generated_at": _now_iso(),
            "summary": summary,
            "partitions": partitions,
        }


# 全局实例
storage_monitor = StorageMonitor()
