"""
Turtlesim WebSocket 端点
实时推送小乌龟的位姿数据
"""
import asyncio
import json
import logging
from fastapi import WebSocket, WebSocketDisconnect
from typing import Set, Optional
from app.services.turtlesim_manager import turtlesim_manager
from app.deps.admin_auth import extract_username_from_session

logger = logging.getLogger(__name__)


class TurtlesimWebSocketManager:
    """Turtlesim WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._callback_registered = False
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_pose: Optional[dict] = None  # 记录上次推送的位姿
        self._pose_change_threshold = {
            'position': 0.01,  # 位置变化阈值（米）
            'angle': 0.01,     # 角度变化阈值（弧度）
            'velocity': 0.01   # 速度变化阈值
        }

    def register_connection(self, websocket: WebSocket):
        """注册 WebSocket 连接"""
        self.active_connections.add(websocket)

        # 保存事件循环引用（用于线程安全的异步调度）
        if self._event_loop is None:
            try:
                self._event_loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("无法获取运行中的事件循环")

        # 如果是第一个连接，注册 pose 回调
        if not self._callback_registered:
            turtlesim_manager.ros_node.register_pose_callback(self._on_pose_update)
            self._callback_registered = True

        logger.info(f"WebSocket 连接已注册，当前连接数: {len(self.active_connections)}")

    def unregister_connection(self, websocket: WebSocket):
        """取消注册 WebSocket 连接"""
        self.active_connections.discard(websocket)

        # 如果没有连接了，取消注册 pose 回调
        if len(self.active_connections) == 0 and self._callback_registered:
            turtlesim_manager.ros_node.unregister_pose_callback(self._on_pose_update)
            self._callback_registered = False

        logger.info(f"WebSocket 连接已取消，当前连接数: {len(self.active_connections)}")

    def _has_pose_changed(self, new_pose: dict) -> bool:
        """检测位姿是否发生显著变化"""
        if self._last_pose is None:
            return True  # 首次推送

        # 检查位置变化
        position_change = (
            abs(new_pose['x'] - self._last_pose['x']) +
            abs(new_pose['y'] - self._last_pose['y'])
        )

        # 检查角度变化
        angle_change = abs(new_pose['theta'] - self._last_pose['theta'])

        # 检查速度变化
        linear_vel_change = abs(
            new_pose.get('linear_velocity', 0.0) -
            self._last_pose.get('linear_velocity', 0.0)
        )
        angular_vel_change = abs(
            new_pose.get('angular_velocity', 0.0) -
            self._last_pose.get('angular_velocity', 0.0)
        )

        # 任何一个超过阈值就认为发生了变化
        return (
            position_change > self._pose_change_threshold['position'] or
            angle_change > self._pose_change_threshold['angle'] or
            linear_vel_change > self._pose_change_threshold['velocity'] or
            angular_vel_change > self._pose_change_threshold['velocity']
        )

    def _on_pose_update(self, pose_data: dict):
        """Pose 更新回调（从 ROS 线程调用）"""
        # 检测位姿是否发生变化
        if not self._has_pose_changed(pose_data):
            return  # 没有显著变化，不推送

        # 更新记录的位姿
        self._last_pose = pose_data.copy()

        # 构造消息
        message = {
            'type': 'pose',
            'x': pose_data['x'],
            'y': pose_data['y'],
            'theta': pose_data['theta'],
            'linear_velocity': pose_data.get('linear_velocity', 0.0),
            'angular_velocity': pose_data.get('angular_velocity', 0.0),
            'timestamp': pose_data['timestamp']
        }

        # 使用线程安全的方式调度异步任务
        if self._event_loop and not self._event_loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._broadcast(message),
                    self._event_loop
                )
            except Exception as e:
                logger.debug(f"调度广播任务失败: {e}")
        else:
            logger.debug("事件循环不可用，跳过广播")

    async def _broadcast(self, message: dict):
        """广播消息给所有连接"""
        disconnected = set()

        for websocket in self.active_connections:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
                disconnected.add(websocket)

        # 移除断开的连接
        for websocket in disconnected:
            self.unregister_connection(websocket)

    async def send_to_all(self, message: dict):
        """发送消息给所有连接（外部调用）"""
        await self._broadcast(message)


# 全局管理器实例
ws_manager = TurtlesimWebSocketManager()


async def turtlesim_websocket_endpoint(websocket: WebSocket):
    """
    Turtlesim WebSocket 端点
    实时推送小乌龟的位姿和速度数据
    """
    # 验证 session
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
    logger.info(f"Turtlesim WebSocket 连接已建立: 用户 {username}")

    # 注册连接
    ws_manager.register_connection(websocket)

    try:
        # 发送初始状态
        status = turtlesim_manager.get_status()
        await websocket.send_json({
            'type': 'status',
            'message': 'Turtlesim WebSocket 已连接',
            'turtlesim_status': status
        })

        # 心跳任务
        async def heartbeat():
            while True:
                try:
                    await asyncio.sleep(30)
                    await websocket.send_json({'type': 'ping'})
                except Exception:
                    break

        heartbeat_task = asyncio.create_task(heartbeat())

        # 处理客户端消息
        while True:
            try:
                message = await websocket.receive_text()
                data = json.loads(message)
                msg_type = data.get('type')

                if msg_type == 'pong':
                    # 心跳响应
                    logger.debug(f"收到心跳响应: {username}")

                elif msg_type == 'get_status':
                    # 获取状态
                    status = turtlesim_manager.get_status()
                    await websocket.send_json({
                        'type': 'status_response',
                        'status': status
                    })

                elif msg_type == 'send_velocity':
                    # 发送速度命令
                    linear = data.get('linear', 0.0)
                    angular = data.get('angular', 0.0)
                    success = turtlesim_manager.send_velocity_command(linear, angular)

                    await websocket.send_json({
                        'type': 'velocity_response',
                        'success': success,
                        'linear': linear,
                        'angular': angular
                    })

                    # 广播速度命令给其他客户端
                    await ws_manager.send_to_all({
                        'type': 'velocity',
                        'linear': linear,
                        'angular': angular,
                        'sender': username
                    })

            except WebSocketDisconnect:
                logger.info(f"Turtlesim WebSocket 断开: 用户 {username}")
                break
            except json.JSONDecodeError:
                logger.warning(f"收到无效的 JSON 数据: {message}")
            except Exception as e:
                logger.error(f"处理 WebSocket 消息时出错: {e}")
                break

    finally:
        # 清理
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        ws_manager.unregister_connection(websocket)
        logger.info(f"Turtlesim WebSocket 连接已清理: 用户 {username}")
