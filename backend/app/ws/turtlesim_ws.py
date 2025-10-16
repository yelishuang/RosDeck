"""
Turtlesim WebSocket endpoint streaming pose and velocity updates.
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
    """Manage WebSocket connections and broadcast turtlesim pose updates."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self._callback_registered = False
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_pose: Optional[dict] = None  # Track the last pose payload sent to clients.
        self._pose_change_threshold = {
            'position': 0.01,  # Position delta threshold (meters).
            'angle': 0.01,     # Angular delta threshold (radians).
            'velocity': 0.01   # Velocity delta threshold.
        }

    def register_connection(self, websocket: WebSocket):
        """Register a WebSocket connection for turtlesim updates."""
        self.active_connections.add(websocket)

        # Cache the event loop for thread-safe scheduling.
        if self._event_loop is None:
            try:
                self._event_loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("无法获取运行中的事件循环")

        # Register the pose callback when the first client connects.
        if not self._callback_registered:
            turtlesim_manager.ros_node.register_pose_callback(self._on_pose_update)
            self._callback_registered = True

        logger.info(f"WebSocket 连接已注册，当前连接数: {len(self.active_connections)}")

    def unregister_connection(self, websocket: WebSocket):
        """Unregister a WebSocket connection."""
        self.active_connections.discard(websocket)

        # Remove the pose callback when the final client disconnects.
        if len(self.active_connections) == 0 and self._callback_registered:
            turtlesim_manager.ros_node.unregister_pose_callback(self._on_pose_update)
            self._callback_registered = False

        logger.info(f"WebSocket 连接已取消，当前连接数: {len(self.active_connections)}")

    def _has_pose_changed(self, new_pose: dict) -> bool:
        """Determine whether the pose delta exceeds broadcast thresholds."""
        if self._last_pose is None:
            return True  # Always broadcast the first pose.

        # Evaluate positional change.
        position_change = (
            abs(new_pose['x'] - self._last_pose['x']) +
            abs(new_pose['y'] - self._last_pose['y'])
        )

        # Evaluate angular change.
        angle_change = abs(new_pose['theta'] - self._last_pose['theta'])

        # Evaluate velocity change.
        linear_vel_change = abs(
            new_pose.get('linear_velocity', 0.0) -
            self._last_pose.get('linear_velocity', 0.0)
        )
        angular_vel_change = abs(
            new_pose.get('angular_velocity', 0.0) -
            self._last_pose.get('angular_velocity', 0.0)
        )

        # Broadcast when any monitored value crosses its threshold.
        return (
            position_change > self._pose_change_threshold['position'] or
            angle_change > self._pose_change_threshold['angle'] or
            linear_vel_change > self._pose_change_threshold['velocity'] or
            angular_vel_change > self._pose_change_threshold['velocity']
        )

    def _on_pose_update(self, pose_data: dict):
        """Pose update callback invoked from the turtlesim ROS thread."""
        # Skip broadcasting when the pose is effectively unchanged.
        if not self._has_pose_changed(pose_data):
            return

        # Store the latest pose snapshot for delta comparisons.
        self._last_pose = pose_data.copy()

        # Prepare the outbound message.
        message = {
            'type': 'pose',
            'x': pose_data['x'],
            'y': pose_data['y'],
            'theta': pose_data['theta'],
            'linear_velocity': pose_data.get('linear_velocity', 0.0),
            'angular_velocity': pose_data.get('angular_velocity', 0.0),
            'timestamp': pose_data['timestamp']
        }

        # Schedule the broadcast back onto the FastAPI event loop.
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
        """Broadcast a JSON payload to every active connection."""
        disconnected = set()

        for websocket in self.active_connections:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
                disconnected.add(websocket)

        # Remove any sockets that failed during sending.
        for websocket in disconnected:
            self.unregister_connection(websocket)

    async def send_to_all(self, message: dict):
        """Expose broadcasting to external callers."""
        await self._broadcast(message)


# Shared manager instance reused across handlers.
ws_manager = TurtlesimWebSocketManager()


async def turtlesim_websocket_endpoint(websocket: WebSocket):
    """Handle a turtlesim WebSocket connection lifecycle."""
    # Validate the session before upgrading the connection.
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

    # Register the connection for future broadcasts.
   ws_manager.register_connection(websocket)

    try:
        # Send the initial turtlesim status snapshot.
        status = turtlesim_manager.get_status()
        await websocket.send_json({
            'type': 'status',
            'message': 'Turtlesim WebSocket 已连接',
            'turtlesim_status': status
        })

        # Heartbeat task keeps the connection active.
        async def heartbeat():
            while True:
                try:
                    await asyncio.sleep(30)
                    await websocket.send_json({'type': 'ping'})
                except Exception:
                    break

        heartbeat_task = asyncio.create_task(heartbeat())

        # Process incoming messages from the client.
        while True:
            try:
                message = await websocket.receive_text()
                data = json.loads(message)
                msg_type = data.get('type')

                if msg_type == 'pong':
                    # Heartbeat acknowledgement.
                    logger.debug(f"收到心跳响应: {username}")

                elif msg_type == 'get_status':
                    # Return current turtlesim status.
                    status = turtlesim_manager.get_status()
                    await websocket.send_json({
                        'type': 'status_response',
                        'status': status
                    })

                elif msg_type == 'send_velocity':
                    # Relay velocity command to turtlesim.
                    linear = data.get('linear', 0.0)
                    angular = data.get('angular', 0.0)
                    success = turtlesim_manager.send_velocity_command(linear, angular)

                    await websocket.send_json({
                        'type': 'velocity_response',
                        'success': success,
                        'linear': linear,
                        'angular': angular
                    })

                    # Broadcast velocity update to all connected clients.
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
        # Cancel heartbeat and unregister the connection.
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        ws_manager.unregister_connection(websocket)
        logger.info(f"Turtlesim WebSocket 连接已清理: 用户 {username}")
