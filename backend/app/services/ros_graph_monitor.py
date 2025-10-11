"""
ROS 2 图谱监控服务
提供实时ROS图谱监控、增量检测、WebSocket推送功能
"""
import asyncio
import copy
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
except ImportError:
    rclpy = None
    SingleThreadedExecutor = None
    Node = None

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """返回当前 UTC ISO 时间字符串"""
    return datetime.now(timezone.utc).isoformat()


class NodeInfo:
    """节点信息"""
    def __init__(self, name: str, namespace: str, first_seen: str):
        self.name = name
        self.namespace = namespace
        self.full_name = f"{namespace}/{name}" if namespace != "/" else f"/{name}"
        self.first_seen = first_seen

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "full_name": self.full_name,
            "first_seen": self.first_seen,
        }

    def __eq__(self, other):
        if not isinstance(other, NodeInfo):
            return False
        return self.full_name == other.full_name

    def __hash__(self):
        return hash(self.full_name)


class TopicInfo:
    """话题信息"""
    def __init__(self, name: str, msg_types: List[str]):
        self.name = name
        self.msg_types = msg_types  # 话题可能有多个类型
        self.publishers: Set[str] = set()
        self.subscribers: Set[str] = set()

    def to_dict(self, include_details: bool = False) -> Dict[str, Any]:
        data = {
            "name": self.name,
            "type": self.msg_types[0] if self.msg_types else "unknown",
            "publisher_count": len(self.publishers),
            "subscriber_count": len(self.subscribers),
        }

        if include_details:
            data["publishers"] = sorted(list(self.publishers))
            data["subscribers"] = sorted(list(self.subscribers))

        return data

    def __eq__(self, other):
        if not isinstance(other, TopicInfo):
            return False
        return (self.name == other.name and
                self.publishers == other.publishers and
                self.subscribers == other.subscribers)

    def __hash__(self):
        return hash((self.name, frozenset(self.publishers), frozenset(self.subscribers)))


class ServiceInfo:
    """服务信息"""
    def __init__(self, name: str, service_types: List[str]):
        self.name = name
        self.service_types = service_types

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.service_types[0] if self.service_types else "unknown",
        }

    def __eq__(self, other):
        if not isinstance(other, ServiceInfo):
            return False
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)


class ROSGraphSnapshot:
    """ROS图谱快照"""
    def __init__(self, snapshot_id: int):
        self.snapshot_id = snapshot_id
        self.timestamp = _now_iso()
        self.nodes: Dict[str, NodeInfo] = {}
        self.topics: Dict[str, TopicInfo] = {}
        self.services: Dict[str, ServiceInfo] = {}
        self.ros_version = ""
        self.dds_impl = ""
        self.domain_id = 0

    def compute_hash(self) -> int:
        """计算快照哈希，用于快速检测变化"""
        return hash((
            frozenset(self.nodes.keys()),
            frozenset(self.topics.values()),
            frozenset(self.services.keys()),
        ))


class ROSGraphMonitor:
    """ROS 2 图谱监控服务"""

    UPDATE_INTERVAL = 2.0  # 2秒更新间隔

    def __init__(self):
        self._node_name = "rosdeck_graph_monitor"
        self._ros_version_hint = self._resolve_ros_version()

        # 快照管理
        self._current_snapshot: Optional[ROSGraphSnapshot] = None
        self._previous_snapshot: Optional[ROSGraphSnapshot] = None
        self._snapshot_id_counter = 0
        self._lock = threading.Lock()

        # 节点首次发现时间追踪
        self._node_first_seen: Dict[str, str] = {}

        # 用户标记（单用户场景，全局共享）
        self._bookmarks: Dict[str, Set[str]] = {
            "nodes": set(),
            "topics": set(),
            "services": set(),
        }

        # WebSocket客户端管理（虽然是单用户，但保留扩展性）
        self._websocket_clients: Set[Any] = set()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # 后台线程
        self._stop_event = threading.Event()
        self._primed_event = threading.Event()
        self._worker: Optional[threading.Thread] = None

        if rclpy is None or SingleThreadedExecutor is None:
            logger.warning("未检测到 rclpy，ROS 图谱监控将不可用")
            self._primed_event.set()
            return

        self._worker = threading.Thread(
            target=self._refresh_loop,
            name="rosdeck-ros-graph-monitor",
            daemon=True
        )
        self._worker.start()

    @staticmethod
    def _resolve_ros_version() -> str:
        """尝试推断 ROS 版本"""
        distro = os.environ.get("ROS_DISTRO", "").strip()
        if distro:
            return f"ROS 2 {distro.capitalize()}"
        return "ROS 2"

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """设置事件循环，用于WebSocket广播"""
        self._event_loop = loop

    def _refresh_loop(self):
        """后台刷新循环：维护 rclpy 节点并定期采样"""
        node = None
        executor = None
        initialized = False

        try:
            # 检查 rclpy 是否已初始化
            if not rclpy.ok():
                rclpy.init(args=None)
                initialized = True
            node = rclpy.create_node(self._node_name)
            executor = SingleThreadedExecutor()
            executor.add_node(node)

            next_sample = 0.0
            while not self._stop_event.is_set():
                executor.spin_once(timeout_sec=0.1)

                if time.monotonic() >= next_sample:
                    snapshot = self._build_graph_snapshot(node)

                    with self._lock:
                        self._previous_snapshot = self._current_snapshot
                        self._current_snapshot = snapshot

                    # 检测变化并广播
                    if self._previous_snapshot is not None:
                        delta = self._detect_changes()
                        if delta:
                            self._broadcast_delta(delta)
                    else:
                        # 首次快照，发送全量数据
                        full_data = self._build_full_data()
                        self._broadcast_delta(full_data)

                    self._primed_event.set()
                    next_sample = time.monotonic() + self.UPDATE_INTERVAL

        except Exception as exc:
            logger.exception("ROS 图谱监控服务启动失败: %s", exc)
            self._primed_event.set()
        finally:
            if executor and node:
                try:
                    executor.remove_node(node)
                except Exception as e:
                    logger.debug("移除节点失败: %s", e)
            if node is not None:
                try:
                    node.destroy_node()
                except Exception as e:
                    logger.debug("销毁节点失败: %s", e)
            # 注意：不要在这里调用 rclpy.shutdown()
            # 因为可能有其他服务（如 ros_monitor）正在使用 rclpy

    def _build_graph_snapshot(self, node) -> ROSGraphSnapshot:
        """构建ROS图谱快照"""
        with self._lock:
            self._snapshot_id_counter += 1
            snapshot = ROSGraphSnapshot(self._snapshot_id_counter)

        snapshot.ros_version = self._ros_version_hint
        snapshot.domain_id = int(os.environ.get("ROS_DOMAIN_ID", "0"))

        # DDS实现检测
        rmw_impl = os.environ.get("RMW_IMPLEMENTATION", "")
        if rmw_impl:
            snapshot.dds_impl = rmw_impl
        else:
            snapshot.dds_impl = "Default"

        # 获取节点列表
        try:
            node_entries = node.get_node_names_and_namespaces()
            for node_name, namespace in node_entries:
                if not node_name or node_name == self._node_name:
                    continue

                full_name = f"{namespace}/{node_name}" if namespace != "/" else f"/{node_name}"

                # 记录首次发现时间
                with self._lock:
                    if full_name not in self._node_first_seen:
                        self._node_first_seen[full_name] = _now_iso()
                    first_seen = self._node_first_seen[full_name]

                node_info = NodeInfo(node_name, namespace, first_seen)
                snapshot.nodes[full_name] = node_info
        except Exception as exc:
            logger.error("获取 ROS 节点列表失败: %s", exc)

        # 获取话题列表
        try:
            topic_entries = node.get_topic_names_and_types()
            for topic_name, msg_types in topic_entries:
                topic_info = TopicInfo(topic_name, msg_types)

                # 获取发布者和订阅者
                try:
                    publishers = node.get_publishers_info_by_topic(topic_name)
                    for pub in publishers:
                        if pub.node_name != self._node_name:
                            pub_full_name = f"{pub.node_namespace}/{pub.node_name}" if pub.node_namespace != "/" else f"/{pub.node_name}"
                            topic_info.publishers.add(pub_full_name)
                except Exception as e:
                    logger.debug(f"获取话题 {topic_name} 发布者失败: {e}")

                try:
                    subscribers = node.get_subscriptions_info_by_topic(topic_name)
                    for sub in subscribers:
                        if sub.node_name != self._node_name:
                            sub_full_name = f"{sub.node_namespace}/{sub.node_name}" if sub.node_namespace != "/" else f"/{sub.node_name}"
                            topic_info.subscribers.add(sub_full_name)
                except Exception as e:
                    logger.debug(f"获取话题 {topic_name} 订阅者失败: {e}")

                snapshot.topics[topic_name] = topic_info
        except Exception as exc:
            logger.error("获取 ROS 话题列表失败: %s", exc)

        # 获取服务列表
        try:
            service_entries = node.get_service_names_and_types()
            for service_name, service_types in service_entries:
                # 过滤掉参数服务（系统内置）
                if "/get_parameters" in service_name or "/set_parameters" in service_name:
                    continue

                service_info = ServiceInfo(service_name, service_types)
                snapshot.services[service_name] = service_info
        except Exception as exc:
            logger.error("获取 ROS 服务列表失败: %s", exc)

        return snapshot

    def _detect_changes(self) -> Optional[Dict[str, Any]]:
        """检测快照变化，返回增量数据"""
        with self._lock:
            if not self._current_snapshot or not self._previous_snapshot:
                return None

            current = self._current_snapshot
            previous = self._previous_snapshot

            # 快速检测：哈希对比
            if current.compute_hash() == previous.compute_hash():
                return None

            # 详细对比
            delta = {
                "type": "delta",
                "snapshot_id": current.snapshot_id,
                "timestamp": current.timestamp,
            }

            # 节点变化
            old_nodes = set(previous.nodes.keys())
            new_nodes = set(current.nodes.keys())

            added_nodes = new_nodes - old_nodes
            removed_nodes = old_nodes - new_nodes

            if added_nodes:
                delta["added_nodes"] = [current.nodes[name].to_dict() for name in added_nodes]
            if removed_nodes:
                delta["removed_nodes"] = list(removed_nodes)

            # 话题变化
            old_topics = set(previous.topics.keys())
            new_topics = set(current.topics.keys())

            added_topics = new_topics - old_topics
            removed_topics = old_topics - new_topics

            if added_topics:
                delta["added_topics"] = [current.topics[name].to_dict() for name in added_topics]
            if removed_topics:
                delta["removed_topics"] = list(removed_topics)

            # 检测话题的发布者/订阅者变化
            updated_topics = []
            for topic_name in old_topics & new_topics:
                old_topic = previous.topics[topic_name]
                new_topic = current.topics[topic_name]

                if old_topic != new_topic:  # 使用 __eq__ 比较
                    updated_topics.append(new_topic.to_dict())

            if updated_topics:
                delta["updated_topics"] = updated_topics

            # 服务变化
            old_services = set(previous.services.keys())
            new_services = set(current.services.keys())

            added_services = new_services - old_services
            removed_services = old_services - new_services

            if added_services:
                delta["added_services"] = [current.services[name].to_dict() for name in added_services]
            if removed_services:
                delta["removed_services"] = list(removed_services)

            # 如果没有任何变化，返回None
            has_changes = any(k in delta for k in [
                "added_nodes", "removed_nodes",
                "added_topics", "removed_topics", "updated_topics",
                "added_services", "removed_services"
            ])

            return delta if has_changes else None

    def _build_full_data(self) -> Dict[str, Any]:
        """构建全量数据"""
        with self._lock:
            if not self._current_snapshot:
                return {"type": "full", "nodes": [], "topics": [], "services": []}

            snapshot = self._current_snapshot

            return {
                "type": "full",
                "snapshot_id": snapshot.snapshot_id,
                "timestamp": snapshot.timestamp,
                "system_info": {
                    "ros_version": snapshot.ros_version,
                    "dds_impl": snapshot.dds_impl,
                    "domain_id": snapshot.domain_id,
                },
                "nodes": [node.to_dict() for node in snapshot.nodes.values()],
                "topics": [topic.to_dict() for topic in snapshot.topics.values()],
                "services": [service.to_dict() for service in snapshot.services.values()],
                "bookmarks": {
                    "nodes": list(self._bookmarks["nodes"]),
                    "topics": list(self._bookmarks["topics"]),
                    "services": list(self._bookmarks["services"]),
                },
            }

    def _broadcast_delta(self, delta: Dict[str, Any]):
        """广播增量数据到所有WebSocket客户端"""
        if not self._event_loop or not self._websocket_clients:
            return

        # 从同步线程调用异步函数
        asyncio.run_coroutine_threadsafe(
            self._async_broadcast(delta),
            self._event_loop
        )

    async def _async_broadcast(self, message: Dict[str, Any]):
        """异步广播消息"""
        dead_clients = set()
        for client in self._websocket_clients:
            try:
                await client.send_json(message)
            except Exception as e:
                logger.debug(f"发送消息到客户端失败: {e}")
                dead_clients.add(client)

        # 清理断开的连接
        for client in dead_clients:
            self._websocket_clients.discard(client)

    def register_websocket(self, websocket):
        """注册WebSocket客户端"""
        self._websocket_clients.add(websocket)
        logger.info(f"WebSocket客户端已注册，当前连接数: {len(self._websocket_clients)}")

    def unregister_websocket(self, websocket):
        """注销WebSocket客户端"""
        self._websocket_clients.discard(websocket)
        logger.info(f"WebSocket客户端已注销，当前连接数: {len(self._websocket_clients)}")

    def get_full_snapshot(self) -> Dict[str, Any]:
        """获取完整快照（用于WebSocket初始连接）"""
        if not self._primed_event.is_set():
            self._primed_event.wait(timeout=2.0)

        return self._build_full_data()

    def get_topic_details(self, topic_name: str) -> Optional[Dict[str, Any]]:
        """获取话题详细信息（懒加载）"""
        with self._lock:
            if not self._current_snapshot:
                return None

            topic = self._current_snapshot.topics.get(topic_name)
            if not topic:
                return None

            return topic.to_dict(include_details=True)

    def add_bookmark(self, entity_type: str, entity_name: str) -> bool:
        """添加用户标记"""
        if entity_type not in self._bookmarks:
            return False

        with self._lock:
            self._bookmarks[entity_type].add(entity_name)

        logger.info(f"添加标记: {entity_type}/{entity_name}")
        return True

    def remove_bookmark(self, entity_type: str, entity_name: str) -> bool:
        """移除用户标记"""
        if entity_type not in self._bookmarks:
            return False

        with self._lock:
            self._bookmarks[entity_type].discard(entity_name)

        logger.info(f"移除标记: {entity_type}/{entity_name}")
        return True

    def get_bookmarks(self) -> Dict[str, List[str]]:
        """获取所有标记"""
        with self._lock:
            return {
                "nodes": list(self._bookmarks["nodes"]),
                "topics": list(self._bookmarks["topics"]),
                "services": list(self._bookmarks["services"]),
            }

    def shutdown(self):
        """关闭监控服务"""
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=3.0)


# 全局实例
ros_graph_monitor = ROSGraphMonitor()
