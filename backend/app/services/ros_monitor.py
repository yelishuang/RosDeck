"""
ROS 2 监控服务
通过长期驻留的 rclpy 节点获取节点/话题/服务统计
"""
import copy
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
except ImportError:  # pragma: no cover - 仅在缺失 ROS 环境时触发
    rclpy = None  # type: ignore
    SingleThreadedExecutor = None  # type: ignore

logger = logging.getLogger(__name__)


class ROSMonitor:
    """ROS 2 监控服务（基于 rclpy 后台节点）"""
    
    def __init__(self):
        self._node_name = "rosdeck_stats_monitor"
        self._update_interval = 5.0  # 刷新间隔（秒）
        self._ros_version_hint = self._resolve_ros_version()
        
        self._stats: Dict[str, Any] = self._default_stats()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._primed_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        
        if rclpy is None or SingleThreadedExecutor is None:
            logger.warning("未检测到 rclpy，ROS 指标将返回默认值")
            self._primed_event.set()
            return
        
        self._worker = threading.Thread(
            target=self._refresh_loop,
            name="rosdeck-ros-monitor",
            daemon=True
        )
        self._worker.start()
    
    def _default_stats(self) -> Dict[str, Any]:
        """构造默认统计数据"""
        return {
            "active_nodes": 0,
            "topics_count": 0,
            "services_count": 0,
            "stability_percent": 0.0,
            "ros_version": self._ros_version_hint,
            "last_updated": datetime.utcnow().isoformat() + "Z"
        }
    
    @staticmethod
    def _resolve_ros_version() -> str:
        """尝试推断 ROS 版本"""
        distro = os.environ.get("ROS_DISTRO", "").strip()
        if distro:
            return f"ROS 2 {distro.capitalize()}"
        return "ROS 2"
    
    def _refresh_loop(self):
        """后台刷新循环：维护 rclpy 节点并定期采样"""
        node = None
        executor = None
        initialized = False
        
        try:
            rclpy.init(args=None)
            initialized = True
            node = rclpy.create_node(self._node_name)
            executor = SingleThreadedExecutor()
            executor.add_node(node)
            
            next_sample = 0.0
            while not self._stop_event.is_set():
                executor.spin_once(timeout_sec=0.1)
                
                if time.monotonic() >= next_sample:
                    stats = self._build_stats_snapshot(node)
                    with self._lock:
                        self._stats = stats
                    self._primed_event.set()
                    next_sample = time.monotonic() + self._update_interval
        except Exception as exc:
            logger.exception("ROS 指标服务启动失败，返回默认数据: %s", exc)
            self._primed_event.set()
        finally:
            if executor and node:
                executor.remove_node(node)
            if node is not None:
                node.destroy_node()
            if initialized:
                try:
                    rclpy.shutdown()
                except Exception as exc:  # pragma: no cover - 仅用于日志
                    logger.warning("关闭 rclpy 失败: %s", exc)
    
    def _build_stats_snapshot(self, node) -> Dict[str, Any]:
        """采集当前 ROS 图谱数据"""
        try:
            node_entries = node.get_node_names_and_namespaces()
            active_nodes = sum(
                1 for name, _ in node_entries
                if name and name != self._node_name
            )
        except Exception as exc:
            logger.error("获取 ROS 节点列表失败: %s", exc)
            active_nodes = 0
        
        try:
            topics = node.get_topic_names_and_types()
            topics_count = len(topics)
        except Exception as exc:
            logger.error("获取 ROS 话题列表失败: %s", exc)
            topics_count = 0
        
        try:
            services = node.get_service_names_and_types()
            services_count = len(services)
        except Exception as exc:
            logger.error("获取 ROS 服务列表失败: %s", exc)
            services_count = 0
        
        stability = self.calculate_stability(active_nodes)
        
        return {
            "active_nodes": active_nodes,
            "topics_count": topics_count,
            "services_count": services_count,
            "stability_percent": stability,
            "ros_version": self._resolve_ros_version(),
            "last_updated": datetime.utcnow().isoformat() + "Z"
        }
    
    @staticmethod
    def calculate_stability(active_nodes: int) -> float:
        """
        计算系统稳定性
        简单策略: 基于节点数量估算
        """
        if active_nodes == 0:
            return 0.0
        if active_nodes <= 5:
            return 95.0
        return 99.8
    
    def get_stats(self) -> Dict[str, Any]:
        """获取完整的 ROS 统计数据"""
        if not self._primed_event.is_set():
            self._primed_event.wait(timeout=1.0)
        
        with self._lock:
            return copy.deepcopy(self._stats)
    
    def shutdown(self):
        """测试或应用关闭时调用"""
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)


# 全局实例
ros_monitor = ROSMonitor()
