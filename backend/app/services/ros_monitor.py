"""
ROS 2 监控服务
通过执行 ROS 2 CLI 命令获取统计数据
"""
import subprocess
import logging
import os
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ROSMonitor:
    """ROS 2 监控服务"""
    
    def __init__(self):
        self._ros_available = None
        self._last_check_time = 0
        self._check_interval = 10  # 每 10 秒检查一次 ROS 可用性
    
    def _is_ros_available(self) -> bool:
        """检查 ROS 2 是否可用"""
        import time
        current_time = time.time()
        
        # 缓存检查结果
        if self._ros_available is not None and \
           (current_time - self._last_check_time) < self._check_interval:
            return self._ros_available
        
        try:
            # 检查 ROS 2 命令是否存在
            result = subprocess.run(
                ['which', 'ros2'],
                capture_output=True,
                timeout=2
            )
            available = result.returncode == 0
            
            self._ros_available = available
            self._last_check_time = current_time
            return available
        except Exception as e:
            logger.warning(f"检查 ROS 2 可用性失败: {e}")
            self._ros_available = False
            self._last_check_time = current_time
            return False
    
    def _run_ros_command(self, command: list, timeout: int = 5) -> str:
        """执行 ROS 2 命令"""
        try:
            # 设置 ROS 2 环境变量(如果需要)
            env = os.environ.copy()
            
            # 执行命令
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env
            )
            
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                logger.error(f"ROS 命令失败: {result.stderr}")
                return ""
        except subprocess.TimeoutExpired:
            logger.error(f"ROS 命令超时: {command}")
            return ""
        except Exception as e:
            logger.error(f"执行 ROS 命令异常: {e}")
            return ""
    
    def get_active_nodes(self) -> int:
        """获取活跃节点数"""
        if not self._is_ros_available():
            return 0
        
        output = self._run_ros_command(['ros2', 'node', 'list'])
        if not output:
            return 0
        
        # 统计非空行数
        nodes = [line for line in output.split('\n') if line.strip()]
        return len(nodes)
    
    def get_topics_count(self) -> int:
        """获取话题数"""
        if not self._is_ros_available():
            return 0
        
        output = self._run_ros_command(['ros2', 'topic', 'list'])
        if not output:
            return 0
        
        topics = [line for line in output.split('\n') if line.strip()]
        return len(topics)
    
    def get_services_count(self) -> int:
        """获取服务数"""
        if not self._is_ros_available():
            return 0
        
        output = self._run_ros_command(['ros2', 'service', 'list'])
        if not output:
            return 0
        
        services = [line for line in output.split('\n') if line.strip()]
        return len(services)
    
    def get_ros_version(self) -> str:
        """获取 ROS 版本"""
        if not self._is_ros_available():
            return "ROS 2 (未检测到)"
        
        # 尝试从环境变量获取
        ros_distro = os.environ.get('ROS_DISTRO', '')
        if ros_distro:
            return f"ROS 2 {ros_distro.capitalize()}"
        
        # 默认返回
        return "ROS 2 Humble"
    
    def calculate_stability(self, active_nodes: int) -> float:
        """
        计算系统稳定性
        简单实现: 基于节点数量判断
        - 0 节点: 0%
        - 1-5 节点: 95%
        - 6+ 节点: 99.8%
        """
        if active_nodes == 0:
            return 0.0
        elif active_nodes <= 5:
            return 95.0
        else:
            return 99.8
    
    def get_stats(self) -> Dict[str, Any]:
        """获取完整的 ROS 统计数据"""
        active_nodes = self.get_active_nodes()
        topics_count = self.get_topics_count()
        services_count = self.get_services_count()
        stability = self.calculate_stability(active_nodes)
        
        return {
            "active_nodes": active_nodes,
            "topics_count": topics_count,
            "services_count": services_count,
            "stability_percent": stability,
            "ros_version": self.get_ros_version(),
            "last_updated": datetime.utcnow().isoformat() + "Z"
        }


# 全局实例
ros_monitor = ROSMonitor()