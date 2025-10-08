"""
ROS 相关路由
"""
from fastapi import APIRouter, HTTPException
import logging
from app.services.ros_monitor import ros_monitor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ros", tags=["ROS"])


@router.get("/stats")
async def get_ros_stats():
    """
    获取 ROS 统计数据
    
    返回:
        - active_nodes: 活跃节点数
        - topics_count: 话题数量
        - services_count: 服务数量
        - stability_percent: 系统稳定性
        - ros_version: ROS 版本
        - last_updated: 最后更新时间
    """
    try:
        stats = ros_monitor.get_stats()
        return stats
    except Exception as e:
        logger.error(f"获取 ROS 统计数据失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取 ROS 统计数据失败", "error": str(e)}
        )