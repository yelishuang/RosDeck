"""
ROS monitoring and bookmark management endpoints.
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
    """Request payload for bookmark mutations."""
    entity_type: str  # Expected values: "nodes", "topics", or "services".
    entity_name: str
    action: str  # Supported actions: "add" or "remove".


@router.get("/stats")
async def get_ros_stats():
    """
    Provide aggregate ROS metrics such as node, topic, and service counts with stability indicators.
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
    Real-time ROS graph monitor WebSocket streaming node, topic, and service updates.
    """
    # Require a valid session cookie before upgrading the connection.
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

    # Lazily bind the monitor to the active event loop.
    if ros_graph_monitor._event_loop is None:
        ros_graph_monitor.set_event_loop(asyncio.get_event_loop())

    # Track the connected WebSocket client.
    ros_graph_monitor.register_websocket(websocket)

    try:
        # Send an initial snapshot to prime the client.
        full_data = ros_graph_monitor.get_full_snapshot()
        await websocket.send_json(full_data)

        # Background task that maintains a heartbeat.
        async def heartbeat():
            while True:
                try:
                    await asyncio.sleep(30)
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break

        heartbeat_task = asyncio.create_task(heartbeat())

        # Process inbound messages until the client disconnects.
        while True:
            try:
                message = await websocket.receive_text()
                data = json.loads(message)
                msg_type = data.get("type")

                if msg_type == "pong":
                    # Handle heartbeat acknowledgements.
                    logger.debug(f"收到心跳响应: {username}")

                elif msg_type == "get_topic_details":
                    # Return topic metadata on demand.
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
                    # Update user-managed bookmarks.
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
        # Cleanup connection state before exiting.
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
    Return publishers and subscribers for the requested topic via HTTP.
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
    Add or remove user bookmarks for ROS entities.
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
    Return every stored bookmark record.
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
