"""
节点生命周期与诊断管理
"""
from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import SingleThreadedExecutor
    from lifecycle_msgs.msg import Transition
    from lifecycle_msgs.srv import ChangeState, GetState, GetAvailableStates
except ImportError:  # pragma: no cover
    rclpy = None  # type: ignore
    Node = None  # type: ignore
    SingleThreadedExecutor = None  # type: ignore
    Transition = None  # type: ignore
    ChangeState = None  # type: ignore
    GetState = None  # type: ignore
    GetAvailableStates = None  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class NodeLifecycleInfo:
    name: str
    namespace: str
    full_name: str
    is_lifecycle: bool
    current_state: Optional[str] = None
    available_states: Optional[List[str]] = None


class _LifecycleUnavailable:
    def list_nodes(self) -> List[NodeLifecycleInfo]:
        return []

    def restart_node(self, node_full_name: str) -> Dict[str, Any]:
        raise RuntimeError("缺少 rclpy 或 lifecycle_msgs，无法执行生命周期操作")

    def get_logs(self, node_name: str, line_limit: int = 200) -> Dict[str, Any]:
        return {"logs": [], "path": None}

    def get_startup_info(self, node_full_name: str) -> Dict[str, Any]:
        raise RuntimeError("缺少 ROS 环境，无法获取节点信息")


class NodeLifecycleManager:
    """管理生命周期节点的启停、日志、状态"""

    def __init__(self):
        if rclpy is None or Node is None:
            logger.warning("未检测到 rclpy，生命周期管理不可用")
            self._impl = _LifecycleUnavailable()
            return

        self._lock = threading.Lock()
        self._node: Optional[Node] = None
        self._executor: Optional[SingleThreadedExecutor] = None
        self._ensure_node()

    def _ensure_node(self):
        with self._lock:
            if self._node is not None:
                return
            if not rclpy.ok():
                rclpy.init(args=None)
            self._node = rclpy.create_node("rosdeck_lifecycle_manager")
            self._executor = SingleThreadedExecutor()
            self._executor.add_node(self._node)

    def list_nodes(self) -> List[NodeLifecycleInfo]:
        if self._node is None:
            return self._impl.list_nodes()  # type: ignore
        node = self._node
        try:
            entries = node.get_node_names_and_namespaces()
        except Exception as exc:
            logger.error("获取节点列表失败: %s", exc)
            return []

        results: List[NodeLifecycleInfo] = []
        for name, namespace in entries:
            if name in ("rosout", "rosdeck_lifecycle_manager"):
                continue
            full_name = self._full_name(name, namespace)
            info = NodeLifecycleInfo(
                name=name,
                namespace=namespace,
                full_name=full_name,
                is_lifecycle=self._is_lifecycle_node(name, namespace),
            )
            if info.is_lifecycle:
                info.current_state = self._get_current_state(full_name)
                info.available_states = self._get_available_states(full_name)
            results.append(info)
        return results

    def restart_node(self, node_full_name: str) -> Dict[str, Any]:
        if self._node is None:
            return self._impl.restart_node(node_full_name)  # type: ignore
        logger.info("尝试重启生命周期节点: %s", node_full_name)
        self._change_state(node_full_name, Transition.TRANSITION_DEACTIVATE)
        self._change_state(node_full_name, Transition.TRANSITION_CLEANUP)
        self._change_state(node_full_name, Transition.TRANSITION_CONFIGURE)
        self._change_state(node_full_name, Transition.TRANSITION_ACTIVATE)
        state = self._get_current_state(node_full_name)
        return {
            "node": node_full_name,
            "state": state,
        }

    def _change_state(self, node_full_name: str, transition_id: int):
        assert self._node is not None
        client = self._node.create_client(ChangeState, f"{node_full_name}/change_state")
        try:
            if not client.wait_for_service(timeout_sec=2.0):
                raise RuntimeError(f"节点 {node_full_name} 不支持生命周期")
            request = ChangeState.Request()
            request.transition.id = transition_id
            future = client.call_async(request)
            rclpy.spin_until_future_complete(self._node, future, timeout_sec=5.0)
            if not future.done():
                raise TimeoutError(f"节点 {node_full_name} 状态切换超时")
            resp = future.result()
            if not resp.success:
                raise RuntimeError(f"节点 {node_full_name} 状态切换失败: {resp.id}")
        finally:
            try:
                self._node.destroy_client(client)
            except Exception:
                pass

    def _get_current_state(self, node_full_name: str) -> Optional[str]:
        assert self._node is not None
        client = self._node.create_client(GetState, f"{node_full_name}/get_state")
        try:
            if not client.wait_for_service(timeout_sec=2.0):
                return None
            request = GetState.Request()
            future = client.call_async(request)
            rclpy.spin_until_future_complete(self._node, future, timeout_sec=3.0)
            if not future.done():
                return None
            resp = future.result()
            return resp.current_state.label
        finally:
            try:
                self._node.destroy_client(client)
            except Exception:
                pass

    def _get_available_states(self, node_full_name: str) -> Optional[List[str]]:
        assert self._node is not None
        client = self._node.create_client(GetAvailableStates, f"{node_full_name}/get_available_states")
        try:
            if not client.wait_for_service(timeout_sec=2.0):
                return None
            request = GetAvailableStates.Request()
            future = client.call_async(request)
            rclpy.spin_until_future_complete(self._node, future, timeout_sec=3.0)
            if not future.done():
                return None
            resp = future.result()
            return [state.label for state in resp.available_states]
        finally:
            try:
                self._node.destroy_client(client)
            except Exception:
                pass

    def _is_lifecycle_node(self, name: str, namespace: str) -> bool:
        assert self._node is not None
        try:
            services = self._node.get_service_names_and_types_by_node(name, namespace)
        except Exception:
            return False
        for service_name, service_types in services:
            if service_name.endswith("change_state") and "lifecycle_msgs/srv/ChangeState" in service_types:
                return True
        return False

    @staticmethod
    def _full_name(name: str, namespace: str) -> str:
        namespace = namespace or "/"
        if namespace.endswith("/"):
            return f"{namespace}{name}"
        return f"{namespace}/{name}"

    # Logs --------------------------------------------------------------------
    def get_logs(self, node_name: str, line_limit: int = 200) -> Dict[str, Any]:
        """从 ~/.ros/log 下获取节点日志"""
        log_dir = Path(os.path.expanduser("~/.ros/log"))
        latest_link = log_dir / "latest"
        paths: List[Path] = []

        if latest_link.exists():
            paths.extend(self._collect_logs(latest_link, node_name))

        # 如果 latest 为空，选最近一次
        if not paths:
            candidates = sorted(log_dir.glob("*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
            for candidate in candidates:
                if candidate.is_dir():
                    paths = self._collect_logs(candidate, node_name)
                    if paths:
                        break

        logs = []
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line_limit:
                    lines = lines[-line_limit:]
                logs.append({
                    "path": str(path),
                    "lines": lines
                })
            except Exception as exc:
                logger.debug("读取日志失败 %s: %s", path, exc)
        return {"logs": logs}

    @staticmethod
    def _collect_logs(base_dir: Path, node_name: str) -> List[Path]:
        pattern = node_name.strip("/").replace("/", "_")
        files = list(base_dir.glob(f"**/*{pattern}*.log"))
        return files

    # Startup info ------------------------------------------------------------
    def get_startup_info(self, node_full_name: str) -> Dict[str, Any]:
        """调用 ros2 node info --include-parameter-services 获取启动信息"""
        cmd = ["ros2", "node", "info", node_full_name, "--include-parameter-services"]
        logger.info("获取节点启动信息: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"获取节点信息失败: {proc.stderr.strip()}")
        return {
            "node": node_full_name,
            "info_text": proc.stdout
        }


# 全局实例
node_lifecycle_manager = NodeLifecycleManager()

