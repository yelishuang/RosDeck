"""
Turtlesim 管理服务
负责启动、监控和控制 turtlesim。
"""
from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import rclpy
    from geometry_msgs.msg import Twist
    from rclpy.node import Node
    from turtlesim.msg import Pose
except ImportError:  # pragma: no cover - 运行环境未安装 ROS 依赖时触发
    rclpy = None  # type: ignore[assignment]
    Node = None  # type: ignore[assignment]
    Twist = None  # type: ignore[assignment]
    Pose = None  # type: ignore[assignment]

try:
    # 避免循环依赖，失败时延迟导入
    from app.services.turtlesim_capture import turtlesim_capture
except Exception:  # pragma: no cover - 单元测试或导入失败时延迟处理
    turtlesim_capture = None  # type: ignore[assignment]


class TurtlesimNode:
    """封装 ROS turtlesim 控制节点的启动与停止。"""

    def __init__(self) -> None:
        self.node: Optional[Node] = None
        self.pose_subscriber = None
        self.cmd_publisher = None
        self.latest_pose: Optional[Dict[str, float]] = None
        self._callbacks: list[Callable[[Dict[str, float]], None]] = []
        self._spin_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> bool:
        if rclpy is None or Node is None:
            logger.error("rclpy 未安装，无法启动 turtlesim ROS 节点")
            return False

        try:
            if not rclpy.ok():
                rclpy.init()

            self.node = rclpy.create_node("rosdeck_turtlesim_controller")
            self.pose_subscriber = self.node.create_subscription(Pose, "/turtle1/pose", self._pose_callback, 10)
            self.cmd_publisher = self.node.create_publisher(Twist, "/turtle1/cmd_vel", 10)

            self._stop_event.clear()
            self._spin_thread = threading.Thread(target=self._spin_loop, name="TurtlesimNodeSpin", daemon=True)
            self._spin_thread.start()

            logger.info("Turtlesim ROS 节点已启动")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("启动 turtlesim ROS 节点失败: %s", exc)
            self.stop()
            return False

    def _spin_loop(self) -> None:
        if rclpy is None or self.node is None:
            return
        try:
            while not self._stop_event.is_set() and rclpy.ok():
                rclpy.spin_once(self.node, timeout_sec=0.1)
        except Exception as exc:  # noqa: BLE001
            logger.error("ROS 自旋循环出错: %s", exc)

    def _pose_callback(self, msg: Pose) -> None:
        snapshot = {
            "x": float(msg.x),
            "y": float(msg.y),
            "theta": float(msg.theta),
            "linear_velocity": float(msg.linear_velocity),
            "angular_velocity": float(msg.angular_velocity),
            "timestamp": time.time(),
        }
        self.latest_pose = snapshot
        for callback in list(self._callbacks):
            try:
                callback(snapshot)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Pose 回调执行失败: %s", exc)

    def register_pose_callback(self, callback: Callable[[Dict[str, float]], None]) -> None:
        if callback not in self._callbacks:
            self._callbacks.append(callback)

    def unregister_pose_callback(self, callback: Callable[[Dict[str, float]], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def send_velocity(self, linear: float, angular: float) -> bool:
        if self.cmd_publisher is None or Twist is None:
            logger.warning("ROS 控制发布者尚未初始化")
            return False
        try:
            msg = Twist()
            msg.linear.x = float(linear)
            msg.angular.z = float(angular)
            self.cmd_publisher.publish(msg)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("发送速度命令失败: %s", exc)
            return False

    def get_latest_pose(self) -> Optional[Dict[str, float]]:
        return self.latest_pose

    def stop(self) -> None:
        self._stop_event.set()
        if self._spin_thread and self._spin_thread.is_alive():
            self._spin_thread.join(timeout=2.0)
        if self.node is not None:
            try:
                self.node.destroy_node()
            except Exception as exc:  # noqa: BLE001
                logger.debug("销毁 ROS 节点失败: %s", exc)
        self.node = None
        self.pose_subscriber = None
        self.cmd_publisher = None
        logger.info("Turtlesim ROS 节点已停止")


class TurtlesimManager:
    """Turtlesim 启停与 ROS 控制一站式服务。"""

    def __init__(self) -> None:
        self.process: Optional[subprocess.Popen[bytes]] = None
        self.ros_node = TurtlesimNode()
        self.is_running = False
        self._motion_thread: Optional[threading.Thread] = None
        self._motion_stop_event = threading.Event()
        self._motion_lock = threading.Lock()
        self._motion_rate_hz = 30.0

    # ------------------------ 启停流程 ------------------------ #
    def start_turtlesim(self) -> Dict[str, Any]:
        if self.is_running:
            return {"success": True, "message": "Turtlesim 已在运行", "already_running": True}

        try:
            if turtlesim_capture is not None:
                turtlesim_capture.reset_cache()
            logger.info("开始启动 turtlesim...")

            env = os.environ.copy()
            self.process = subprocess.Popen(
                ["ros2", "run", "turtlesim", "turtlesim_node"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            logger.info("turtlesim 进程已启动，PID=%s", self.process.pid)

            if not self._wait_for_process(timeout=6.0):
                raise RuntimeError("turtlesim_node 进程启动异常或提前退出")

            logger.info("启动 ROS 控制节点...")
            if not self.ros_node.start():
                raise RuntimeError("ROS 节点启动失败")

            if not self._wait_for_pose(timeout=10.0):
                raise RuntimeError("/turtle1/pose 未在规定时间内产生数据")

            self.is_running = True
            logger.info("✓ Turtlesim 已启动")

            return {
                "success": True,
                "message": "Turtlesim 启动成功",
                "pid": self.process.pid if self.process else None,
            }

        except Exception as exc:  # noqa: BLE001
            logger.error("✗ 启动 turtlesim 失败: %s", exc)
            self.cleanup()
            return {"success": False, "message": f"启动失败: {exc}"}

    def stop_turtlesim(self) -> Dict[str, Any]:
        if not self.is_running:
            return {"success": True, "message": "Turtlesim 未在运行"}
        try:
            self.cleanup()
            self.is_running = False
            logger.info("Turtlesim 已停止")
            return {"success": True, "message": "Turtlesim 已停止"}
        except Exception as exc:  # noqa: BLE001
            logger.error("停止 turtlesim 失败: %s", exc)
            return {"success": False, "message": f"停止失败: {exc}"}

    def cleanup(self) -> None:
        self.stop_motion(wait=True)
        self.ros_node.stop()
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5.0)
            except Exception:  # noqa: BLE001
                self.process.kill()
            finally:
                self.process = None
        if turtlesim_capture is not None:
            turtlesim_capture.reset_cache()

    # ------------------------ 辅助工具 ------------------------ #
    def _wait_for_process(self, timeout: float) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self.process and self.process.poll() is None:
                time.sleep(0.4)  # 再等待片刻确保稳定
                return True
            time.sleep(0.2)
        return False

    def _wait_for_pose(self, timeout: float) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if self.ros_node.get_latest_pose() is not None:
                logger.info("已收到 turtlesim 位姿数据")
                return True
            time.sleep(0.3)
        logger.warning("等待 /turtle1/pose 数据超时")
        return False

    # ------------------------ 对外 API ------------------------ #
    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "has_process": self.process is not None and self.process.poll() is None,
            "latest_pose": self.ros_node.get_latest_pose(),
        }

    def send_velocity_command(self, linear: float, angular: float) -> bool:
        return self.ros_node.send_velocity(linear, angular)

    # ------------------------ 动作计划执行 ------------------------ #
    def stop_motion(self, wait: bool = False) -> None:
        with self._motion_lock:
            if self._motion_thread and self._motion_thread.is_alive():
                logger.info("停止当前动作计划执行线程")
                self._motion_stop_event.set()
                if wait:
                    self._motion_thread.join(timeout=3.0)
            self._motion_thread = None
            if wait or self._motion_stop_event.is_set():
                self._motion_stop_event = threading.Event()
        # 确保停止后发送零速度
        if self.is_running:
            self.ros_node.send_velocity(0.0, 0.0)

    def execute_motion_plan(self, commands: List[Dict[str, Any]]) -> None:
        if not self.is_running:
            raise RuntimeError("Turtlesim 未运行，无法执行动作计划")
        if not commands:
            raise ValueError("动作计划为空")

        self.stop_motion(wait=True)
        with self._motion_lock:
            self._motion_stop_event = threading.Event()
            self._motion_thread = threading.Thread(
                target=self._run_motion_plan,
                name="TurtlesimMotionPlan",
                args=(commands, self._motion_stop_event),
                daemon=True,
            )
            self._motion_thread.start()
            logger.info("动作计划已提交，共 %s 步", len(commands))

    def _run_motion_plan(
        self,
        commands: List[Dict[str, Any]],
        stop_event: threading.Event,
    ) -> None:
        logger.info("开始执行动作计划，共 %s 步", len(commands))
        try:
            for index, command in enumerate(commands, start=1):
                if stop_event.is_set():
                    logger.info("动作计划在第 %s 步之前被取消", index)
                    break
                self._execute_single_command(command, index, len(commands), stop_event)
                if stop_event.is_set():
                    logger.info("动作计划在执行过程中被取消")
                    break
        except Exception as exc:  # noqa: BLE001
            logger.error("执行动作计划时出现异常: %s", exc)
        finally:
            self.ros_node.send_velocity(0.0, 0.0)
            with self._motion_lock:
                self._motion_thread = None
                if stop_event.is_set():
                    logger.info("动作计划执行线程已结束（被取消）")
                else:
                    logger.info("动作计划执行完成")

    def _execute_single_command(
        self,
        command: Dict[str, Any],
        index: int,
        total: int,
        stop_event: threading.Event,
    ) -> None:
        action = (command.get("action") or "").strip().lower()
        params = command.get("params") or {}
        logger.info("执行动作计划步骤 %s/%s: %s %s", index, total, action, params)

        if action == "move":
            self._handle_move(params, stop_event)
        elif action == "rotate":
            self._handle_rotate(params, stop_event)
        elif action == "stop":
            self.ros_node.send_velocity(0.0, 0.0)
            time.sleep(0.05)
        else:
            logger.warning("未知的动作类型 '%s'，跳过", action)

    def _handle_move(self, params: Dict[str, Any], stop_event: threading.Event) -> None:
        try:
            speed = float(params.get("linear_speed", 0.0))
            distance = float(params.get("distance", 0.0))
        except (TypeError, ValueError):
            logger.warning("move 动作参数无法解析: %s", params)
            return

        if speed <= 0 or distance <= 0:
            logger.warning("move 动作参数不合法（速度或距离<=0）：%s", params)
            return

        is_forward = bool(params.get("is_forward", True))
        duration = distance / speed if speed > 0 else 0.0
        linear_velocity = speed if is_forward else -speed
        logger.info(
            "move 动作：速度 %.3f m/s，距离 %.3f m，时长 %.3f s，方向=%s",
            linear_velocity,
            distance,
            duration,
            "forward" if is_forward else "backward",
        )
        self._publish_velocity_for_duration(
            duration=duration,
            linear=linear_velocity,
            angular=0.0,
            stop_event=stop_event,
        )

    def _handle_rotate(self, params: Dict[str, Any], stop_event: threading.Event) -> None:
        try:
            angular_velocity_deg = float(params.get("angular_velocity", 0.0))
            angle_deg = float(params.get("angle", 0.0))
        except (TypeError, ValueError):
            logger.warning("rotate 动作参数无法解析: %s", params)
            return

        if angular_velocity_deg <= 0 or abs(angle_deg) <= 0:
            logger.warning("rotate 动作参数不合法（角速度或角度<=0）：%s", params)
            return

        is_clockwise = bool(params.get("is_clockwise", True))
        angle = abs(angle_deg)
        duration = angle / angular_velocity_deg if angular_velocity_deg > 0 else 0.0
        angular_velocity = math.radians(angular_velocity_deg)
        angular_velocity = -angular_velocity if is_clockwise else angular_velocity
        logger.info(
            "rotate 动作：角速度 %.3f rad/s（%.3f deg/s），角度 %.3f deg，时长 %.3f s，方向=%s",
            angular_velocity,
            angular_velocity_deg,
            angle,
            duration,
            "clockwise" if is_clockwise else "counter-clockwise",
        )
        self._publish_velocity_for_duration(
            duration=duration,
            linear=0.0,
            angular=angular_velocity,
            stop_event=stop_event,
        )

    def _publish_velocity_for_duration(
        self,
        *,
        duration: float,
        linear: float,
        angular: float,
        stop_event: threading.Event,
    ) -> None:
        end_time = time.monotonic() + max(0.0, duration)
        period = 1.0 / self._motion_rate_hz

        while time.monotonic() < end_time:
            if stop_event.is_set():
                logger.debug("收到取消信号，提前结束动作")
                break
            self.ros_node.send_velocity(linear, angular)
            time.sleep(period)

        # 最终发送一次零速度确保停下
        self.ros_node.send_velocity(0.0, 0.0)
        time.sleep(0.05)

# 全局实例供路由与 WebSocket 复用
turtlesim_manager = TurtlesimManager()
