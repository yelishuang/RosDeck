"""
ROS 相关路由
"""
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
import asyncio
import json
import logging
from pydantic import BaseModel
from app.services.ros_monitor import ros_monitor
from app.services.ros_graph_monitor import ros_graph_monitor
from app.deps.admin_auth import extract_username_from_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ros", tags=["ROS"])


class BookmarkRequest(BaseModel):
    """标记请求"""
    entity_type: str  # "nodes" | "topics" | "services"
    entity_name: str
    action: str  # "add" | "remove"


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


@router.websocket("/ws")
async def ros_graph_websocket(websocket: WebSocket):
    """
    ROS图谱监控 WebSocket 连接
    实时推送ROS图谱变化（节点、话题、服务）
    """
    # 验证session（可选，根据需求决定是否需要认证）
    cookie_session_id = websocket.cookies.get("session_id")
    if not cookie_session_id:
        logger.warning("WebSocket handshake rejected: missing session_id cookie")
        await websocket.close(code=4401)
        return

    try:
        username = extract_username_from_session(cookie_session_id)
    except ValueError as exc:
        logger.warning(f"WebSocket handshake rejected: invalid session_id ({exc})")
        await websocket.close(code=4401)
        return

    await websocket.accept()
    logger.info(f"ROS图谱 WebSocket 连接已建立: 用户 {username}")

    # 设置事件循环（首次连接时）
    if ros_graph_monitor._event_loop is None:
        ros_graph_monitor.set_event_loop(asyncio.get_event_loop())

    # 注册客户端
    ros_graph_monitor.register_websocket(websocket)

    try:
        # 发送初始全量数据
        full_data = ros_graph_monitor.get_full_snapshot()
        await websocket.send_json(full_data)

        # 心跳任务
        async def heartbeat():
            while True:
                try:
                    await asyncio.sleep(30)
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break

        heartbeat_task = asyncio.create_task(heartbeat())

        # 处理客户端消息
        while True:
            try:
                message = await websocket.receive_text()
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "pong":
                    # 心跳响应
                    logger.debug(f"收到心跳响应: {username}")

                elif msg_type == "get_topic_details":
                    # 获取话题详细信息
                    topic_name = data.get("topic")
                    if topic_name:
                        details = ros_graph_monitor.get_topic_details(topic_name)
                        if details:
                            await websocket.send_json({
                                "type": "topic_details",
                                "topic": topic_name,
                                "details": details
                            })
                        else:
                            await websocket.send_json({
                                "type": "error",
                                "message": f"话题 {topic_name} 不存在"
                            })

                elif msg_type == "bookmark":
                    # 用户标记
                    entity_type = data.get("entity_type")
                    entity_name = data.get("entity_name")
                    action = data.get("action")

                    if action == "add":
                        success = ros_graph_monitor.add_bookmark(entity_type, entity_name)
                    elif action == "remove":
                        success = ros_graph_monitor.remove_bookmark(entity_type, entity_name)
                    else:
                        success = False

                    if success:
                        bookmarks = ros_graph_monitor.get_bookmarks()
                        await websocket.send_json({
                            "type": "bookmark_updated",
                            "bookmarks": bookmarks
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": "标记操作失败"
                        })

            except WebSocketDisconnect:
                logger.info(f"ROS图谱 WebSocket 断开: 用户 {username}")
                break
            except json.JSONDecodeError:
                logger.warning(f"收到无效的JSON数据: {message}")
            except Exception as e:
                logger.error(f"处理WebSocket消息时出错: {e}")
                break

    finally:
        # 清理
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        ros_graph_monitor.unregister_websocket(websocket)
        logger.info(f"ROS图谱 WebSocket 连接已清理: 用户 {username}")


@router.get("/topics/{topic_name}/details")
async def get_topic_details(topic_name: str):
    """
    获取话题详细信息（发布者和订阅者列表）
    用于HTTP方式获取（WebSocket方式更推荐）
    """
    try:
        details = ros_graph_monitor.get_topic_details(topic_name)
        if details:
            return {"success": True, "details": details}
        else:
            raise HTTPException(
                status_code=404,
                detail={"message": f"话题 {topic_name} 不存在"}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取话题详情失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取话题详情失败", "error": str(e)}
        )


@router.post("/bookmark")
async def manage_bookmark(request: BookmarkRequest):
    """
    管理用户标记
    """
    try:
        if request.action == "add":
            success = ros_graph_monitor.add_bookmark(request.entity_type, request.entity_name)
        elif request.action == "remove":
            success = ros_graph_monitor.remove_bookmark(request.entity_type, request.entity_name)
        else:
            raise HTTPException(
                status_code=400,
                detail={"message": "无效的操作类型，必须是 add 或 remove"}
            )

        if success:
            bookmarks = ros_graph_monitor.get_bookmarks()
            return {"success": True, "bookmarks": bookmarks}
        else:
            raise HTTPException(
                status_code=400,
                detail={"message": "标记操作失败，请检查 entity_type 是否有效"}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"管理标记失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "管理标记失败", "error": str(e)}
        )


@router.get("/bookmarks")
async def get_bookmarks():
    """
    获取所有用户标记
    """
    try:
        bookmarks = ros_graph_monitor.get_bookmarks()
        return {"success": True, "bookmarks": bookmarks}
    except Exception as e:
        logger.error(f"获取标记列表失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取标记列表失败", "error": str(e)}
        )