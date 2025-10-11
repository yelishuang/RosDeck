"""
ROS 参数管理服务
"""
from __future__ import annotations

import copy
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from rcl_interfaces.msg import ParameterDescriptor, ParameterEvent, ParameterType, ParameterValue, Parameter as ParameterMsg
    from rcl_interfaces.srv import DescribeParameters, GetParameters, ListParameters
except ImportError:  # pragma: no cover - 缺少 ROS 环境时触发
    rclpy = None  # type: ignore
    SingleThreadedExecutor = None  # type: ignore
    Node = None  # type: ignore
    Parameter = None  # type: ignore
    ParameterDescriptor = None  # type: ignore
    ParameterEvent = None  # type: ignore
    ParameterType = None  # type: ignore
    ParameterValue = None  # type: ignore
    DescribeParameters = None  # type: ignore
    GetParameters = None  # type: ignore
    ListParameters = None  # type: ignore

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _param_value_to_python(value: Optional[ParameterValue]) -> Any:
    """将 ParameterValue 转换为 Python 类型"""
    if value is None:
        return None
    if value.type == ParameterType.PARAMETER_BOOL:
        return value.bool_value
    if value.type == ParameterType.PARAMETER_INTEGER:
        return value.integer_value
    if value.type == ParameterType.PARAMETER_DOUBLE:
        return value.double_value
    if value.type == ParameterType.PARAMETER_STRING:
        return value.string_value
    if value.type == ParameterType.PARAMETER_BYTE_ARRAY:
        return list(value.byte_array_value)
    if value.type == ParameterType.PARAMETER_BOOL_ARRAY:
        return list(value.bool_array_value)
    if value.type == ParameterType.PARAMETER_INTEGER_ARRAY:
        return list(value.integer_array_value)
    if value.type == ParameterType.PARAMETER_DOUBLE_ARRAY:
        return list(value.double_array_value)
    if value.type == ParameterType.PARAMETER_STRING_ARRAY:
        return list(value.string_array_value)
    if value.type == ParameterType.PARAMETER_NOT_SET:
        return None
    return None


def _parameter_to_dict(name: str, value: ParameterValue, descriptor: Optional[ParameterDescriptor]) -> Dict[str, Any]:
    description = descriptor.description if descriptor else ""
    read_only = descriptor.read_only if descriptor else False
    if value is not None and hasattr(value, "type"):
        try:
            type_name = ParameterType(value.type).name
        except Exception:
            type_name = str(value.type)
    else:
        type_name = "PARAMETER_NOT_SET"
    return {
        "name": name,
        "type": type_name,
        "value": _param_value_to_python(value),
        "descriptor": {
            "description": description,
            "read_only": read_only,
            "additional_constraints": descriptor.additional_constraints if descriptor else "",
        }
    }


def _build_tree(parameters: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """将参数字典构建为树形结构"""
    root: Dict[str, Any] = {"name": "/", "children": {}, "is_leaf": False}
    for name, info in parameters.items():
        parts = name.split(".")
        node = root
        for idx, part in enumerate(parts):
            if part not in node["children"]:
                node["children"][part] = {"name": part, "children": {}, "is_leaf": False}
            node = node["children"][part]
            if idx == len(parts) - 1:
                node["is_leaf"] = True
                node["parameter"] = info
    return root["children"]


@dataclass
class ParameterEventRecord:
    timestamp: float
    node: str
    added: List[Dict[str, Any]]
    changed: List[Dict[str, Any]]
    deleted: List[Dict[str, Any]]


class _StubParameterManager:
    """缺少 ROS 环境时的降级实现"""

    def is_available(self) -> bool:
        return False

    def list_nodes(self) -> List[Dict[str, str]]:
        return []

    def get_node_parameters(self, node_name: str) -> Dict[str, Any]:
        raise RuntimeError("ROS 参数功能不可用：未检测到 rclpy")

    def get_all_parameters(self) -> Dict[str, Any]:
        return {}

    def get_tree_view(self) -> Dict[str, Any]:
        return {}

    def get_recent_events(self, since_ts: Optional[float] = None) -> List[ParameterEventRecord]:
        return []

    def export_snapshot(self) -> Dict[str, Any]:
        return {}


class ROSParameterManager:
    """
    ROS2 参数管理
    维护后台 rclpy 节点用于参数查询、事件监听
    """

    MAX_EVENT_HISTORY = 512

    def __init__(self):
        if rclpy is None or SingleThreadedExecutor is None or Node is None:
            logger.warning("未检测到 rclpy，参数管理功能不可用")
            self._impl = _StubParameterManager()
            return

        self._lock = threading.Lock()
        self._node: Optional[Node] = None
        self._executor: Optional[SingleThreadedExecutor] = None
        self._stop_event = threading.Event()
        self._primed_event = threading.Event()

        self._parameter_cache: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
        self._descriptor_cache: Dict[str, Dict[str, ParameterDescriptor]] = defaultdict(dict)
        self._event_history: List[ParameterEventRecord] = []

        self._distro = os.environ.get("ROS_DISTRO", "humble")

        self._worker = threading.Thread(target=self._run, name="rosdeck-parameter-manager", daemon=True)
        self._worker.start()

    # Public API -----------------------------------------------------
    def is_available(self) -> bool:
        return rclpy is not None and self._primed_event.is_set()

    def list_nodes(self) -> List[Dict[str, str]]:
        if not self.is_available():
            return []
        with self._lock:
            nodes = []
            for node_full, params in self._parameter_cache.items():
                entry = params.get("__meta__", {})
                nodes.append({
                    "name": entry.get("name", node_full),
                    "namespace": entry.get("namespace", "/"),
                    "full_name": node_full,
                    "parameter_count": entry.get("parameter_count", len(params) - 1 if "__meta__" in params else len(params))
                })
            nodes.sort(key=lambda item: item["full_name"])
            return nodes

    def get_node_parameters(self, node_full_name: str) -> Dict[str, Any]:
        if not self.is_available():
            raise RuntimeError("ROS 参数功能不可用：rclpy 未初始化")
        node_data = self._fetch_parameters_for_node(node_full_name)
        tree = _build_tree(node_data)
        return {
            "node": node_full_name,
            "flat": node_data,
            "tree": tree,
        }

    def get_all_parameters(self) -> Dict[str, Any]:
        if not self.is_available():
            return {}
        snapshot = {}
        with self._lock:
            for node, params in self._parameter_cache.items():
                snapshot[node] = {
                    key: value for key, value in params.items()
                    if key != "__meta__"
                }
        return snapshot

    def get_tree_view(self) -> Dict[str, Any]:
        all_params = self.get_all_parameters()
        tree_map = {}
        for node, params in all_params.items():
            tree_map[node] = _build_tree(params)
        return tree_map

    def get_recent_events(self, since_ts: Optional[float] = None) -> List[ParameterEventRecord]:
        if not self.is_available():
            return []
        with self._lock:
            if since_ts is None:
                return copy.deepcopy(self._event_history)
            return [copy.deepcopy(ev) for ev in self._event_history if ev.timestamp > since_ts]

    def export_snapshot(self) -> Dict[str, Any]:
        """导出全部节点参数快照"""
        data = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ros_distro": self._distro,
            "nodes": self.get_all_parameters()
        }
        return data

    def shutdown(self):
        if rclpy is None:
            return
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)

    # Internal -------------------------------------------------------
    def _run(self):
        node: Optional[Node] = None
        executor: Optional[SingleThreadedExecutor] = None
        initialized = False
        try:
            if not rclpy.ok():
                rclpy.init(args=None)
                initialized = True
            node = rclpy.create_node("rosdeck_parameter_manager")
            executor = SingleThreadedExecutor()
            executor.add_node(node)

            self._node = node
            self._executor = executor

            # 订阅参数事件
            node.create_subscription(
                ParameterEvent,
                "/parameter_events",
                self._handle_parameter_event,
                10
            )

            # 首次加载
            self._refresh_all_nodes()
            self._primed_event.set()

            next_refresh = time.monotonic() + 15.0
            while not self._stop_event.is_set():
                executor.spin_once(timeout_sec=0.2)
                if time.monotonic() >= next_refresh:
                    self._refresh_all_nodes()
                    next_refresh = time.monotonic() + 15.0
        except Exception as exc:
            logger.exception("参数管理服务启动失败: %s", exc)
            self._primed_event.set()
        finally:
            if executor and node:
                try:
                    executor.remove_node(node)
                except Exception:
                    pass
            if node:
                try:
                    node.destroy_node()
                except Exception:
                    pass
            if initialized:
                try:
                    rclpy.shutdown()
                except Exception:
                    pass

    def _refresh_all_nodes(self):
        if self._node is None:
            return
        try:
            node_entries = self._node.get_node_names_and_namespaces()
        except Exception as exc:
            logger.error("获取节点列表失败: %s", exc)
            return

        for name, namespace in node_entries:
            if name in ("rosout", "rosdeck_parameter_manager"):
                continue
            full_name = self._make_full_name(name, namespace)
            try:
                self._fetch_parameters_for_node(full_name, cache_only=False, metadata={"name": name, "namespace": namespace})
            except Exception as exc:
                logger.debug("刷新节点参数失败 %s: %s", full_name, exc)
                continue

    @staticmethod
    def _make_full_name(name: str, namespace: str) -> str:
        namespace = namespace or "/"
        if namespace.endswith("/"):
            return f"{namespace}{name}"
        return f"{namespace}/{name}"

    def _fetch_parameters_for_node(
        self,
        node_full_name: str,
        cache_only: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            cached = self._parameter_cache.get(node_full_name)

        if cache_only and cached is not None:
            return copy.deepcopy(cached)

        if self._node is None:
            raise RuntimeError("参数管理节点未初始化")

        list_service_name = f"{node_full_name}/list_parameters"
        describe_service_name = f"{node_full_name}/describe_parameters"
        get_service_name = f"{node_full_name}/get_parameters"

        list_client = self._node.create_client(ListParameters, list_service_name)
        describe_client = self._node.create_client(DescribeParameters, describe_service_name)
        get_client = self._node.create_client(GetParameters, get_service_name)

        try:
            if not list_client.wait_for_service(timeout_sec=2.0):
                raise RuntimeError(f"节点 {node_full_name} 不支持参数服务")
            if not get_client.wait_for_service(timeout_sec=2.0):
                raise RuntimeError(f"节点 {node_full_name} 不支持参数查询")

            list_req = ListParameters.Request()
            list_req.depth = 10

            future = list_client.call_async(list_req)
            rclpy.spin_until_future_complete(self._node, future, timeout_sec=3.0)
            if not future.done():
                raise TimeoutError(f"获取节点 {node_full_name} 参数列表超时")
            list_resp = future.result()
            parameter_names = list(list_resp.names)

            # 获取参数值
            get_req = GetParameters.Request()
            get_req.names = parameter_names
            future = get_client.call_async(get_req)
            rclpy.spin_until_future_complete(self._node, future, timeout_sec=3.0)
            if not future.done():
                raise TimeoutError(f"获取节点 {node_full_name} 参数值超时")
            get_resp = future.result()

            # 描述符可选
            descriptors: List[ParameterDescriptor] = []
            if describe_client.wait_for_service(timeout_sec=1.0):
                describe_req = DescribeParameters.Request()
                describe_req.names = parameter_names
                future = describe_client.call_async(describe_req)
                rclpy.spin_until_future_complete(self._node, future, timeout_sec=3.0)
                if future.done():
                    describe_resp = future.result()
                    descriptors = list(describe_resp.descriptors)

            descriptor_map: Dict[str, ParameterDescriptor] = {}
            for descriptor in descriptors:
                descriptor_map[descriptor.name] = descriptor

            param_map: Dict[str, Dict[str, Any]] = {}
            for idx, name in enumerate(parameter_names):
                value = get_resp.values[idx] if idx < len(get_resp.values) else None
                param_map[name] = _parameter_to_dict(name, value, descriptor_map.get(name))

            if metadata:
                param_map["__meta__"] = {
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "parameter_count": len(parameter_names),
                }

            with self._lock:
                self._parameter_cache[node_full_name] = param_map
                self._descriptor_cache[node_full_name] = descriptor_map

            return copy.deepcopy(param_map)
        finally:
            try:
                self._node.destroy_client(list_client)
                self._node.destroy_client(describe_client)
                self._node.destroy_client(get_client)
            except Exception:
                pass

    def _handle_parameter_event(self, event: ParameterEvent):
        node = event.node
        added = [parameter_to_python(x) for x in event.new_parameters]
        changed = [parameter_to_python(x) for x in event.changed_parameters]
        deleted = [parameter_to_python(x) for x in event.deleted_parameters]

        record = ParameterEventRecord(
            timestamp=_now(),
            node=node,
            added=added,
            changed=changed,
            deleted=deleted
        )

        with self._lock:
            self._event_history.append(record)
            if len(self._event_history) > self.MAX_EVENT_HISTORY:
                self._event_history = self._event_history[-self.MAX_EVENT_HISTORY:]

            # 更新缓存
            cache = self._parameter_cache.setdefault(node, {})
            descriptor_cache = self._descriptor_cache.setdefault(node, {})
            for item in added + changed:
                cache[item["name"]] = item
            for item in deleted:
                cache.pop(item["name"], None)


def parameter_to_python(parameter_msg) -> Dict[str, Any]:
    """Parameter 描述转换"""
    descriptor = getattr(parameter_msg, "descriptor", None)
    value = getattr(parameter_msg, "value", None)
    return _parameter_to_dict(parameter_msg.name, value, descriptor)


# 全局实例
ros_parameter_manager = ROSParameterManager()
