"""
ROS 2 graph monitoring service delivering snapshots and real-time deltas over WebSockets.
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
    """Return the current UTC timestamp as an ISO 8601 formatted string."""
    return datetime.now(timezone.utc).isoformat()


class NodeInfo:
    """Represents a ROS node discovered within the graph."""
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
    """Represents a ROS topic along with publisher and subscriber sets."""
    def __init__(self, name: str, msg_types: List[str]):
        self.name = name
        self.msg_types = msg_types  # Topics can expose multiple message types.
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
    """Representation of a ROS service endpoint."""
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
    """Immutable snapshot of the ROS graph state."""
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
        """Generate a hash to quickly detect structural changes."""
        return hash((
            frozenset(self.nodes.keys()),
            frozenset(self.topics.values()),
            frozenset(self.services.keys()),
        ))


class ROSGraphMonitor:
    """Continuously tracks the ROS graph and distributes deltas to subscribed clients."""

    UPDATE_INTERVAL = 2.0  # Refresh interval in seconds.

    def __init__(self):
        self._node_name = "rosdeck_graph_monitor"
        self._ros_version_hint = self._resolve_ros_version()

        # Snapshot bookkeeping.
        self._current_snapshot: Optional[ROSGraphSnapshot] = None
        self._previous_snapshot: Optional[ROSGraphSnapshot] = None
        self._snapshot_id_counter = 0
        self._lock = threading.Lock()

        # Track the initial discovery timestamp for each node.
        self._node_first_seen: Dict[str, str] = {}

        # Bookmark storage (shared in the single-user deployment model).
        self._bookmarks: Dict[str, Set[str]] = {
            "nodes": set(),
            "topics": set(),
            "services": set(),
        }

        # Manage connected WebSocket clients (designed for potential multi-user growth).
        self._websocket_clients: Set[Any] = set()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

        # Background worker management.
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
        """Attempt to derive the ROS distribution version from the environment."""
        distro = os.environ.get("ROS_DISTRO", "").strip()
        if distro:
            return f"ROS 2 {distro.capitalize()}"
        return "ROS 2"

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Assign the event loop used to dispatch WebSocket notifications."""
        self._event_loop = loop

    def _refresh_loop(self):
        """Background refresh loop responsible for maintaining state and sampling the graph."""
        node = None
        executor = None
        initialized = False

        try:
            # Ensure rclpy is initialised before interacting with the graph.
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

                    # Detect changes and broadcast to connected clients.
                    if self._previous_snapshot is not None:
                        delta = self._detect_changes()
                        if delta:
                            self._broadcast_delta(delta)
                    else:
                        # First snapshot for this session; push full graph state.
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
            # Do not call rclpy.shutdown() here - other services (e.g. ros_monitor) may still rely on it.

    def _build_graph_snapshot(self, node) -> ROSGraphSnapshot:
        """Construct a ROS graph snapshot using the provided rclpy node."""
        with self._lock:
            self._snapshot_id_counter += 1
            snapshot = ROSGraphSnapshot(self._snapshot_id_counter)

        snapshot.ros_version = self._ros_version_hint
        snapshot.domain_id = int(os.environ.get("ROS_DOMAIN_ID", "0"))

        # Detect the DDS implementation if exposed in the environment.
        rmw_impl = os.environ.get("RMW_IMPLEMENTATION", "")
        if rmw_impl:
            snapshot.dds_impl = rmw_impl
        else:
            snapshot.dds_impl = "Default"

        # Populate node details.
        try:
            node_entries = node.get_node_names_and_namespaces()
            for node_name, namespace in node_entries:
                if not node_name or node_name == self._node_name:
                    continue

                full_name = f"{namespace}/{node_name}" if namespace != "/" else f"/{node_name}"

                # Record the first time we observed this node.
                with self._lock:
                    if full_name not in self._node_first_seen:
                        self._node_first_seen[full_name] = _now_iso()
                    first_seen = self._node_first_seen[full_name]

                node_info = NodeInfo(node_name, namespace, first_seen)
                snapshot.nodes[full_name] = node_info
        except Exception as exc:
            logger.error("获取 ROS 节点列表失败: %s", exc)

        # Populate topic details.
        try:
            topic_entries = node.get_topic_names_and_types()
            for topic_name, msg_types in topic_entries:
                topic_info = TopicInfo(topic_name, msg_types)

                # Capture publishers and subscribers.
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

        # Populate service details.
        try:
            service_entries = node.get_service_names_and_types()
            for service_name, service_types in service_entries:
                # Exclude parameter services that are part of the core runtime.
                if "/get_parameters" in service_name or "/set_parameters" in service_name:
                    continue

                service_info = ServiceInfo(service_name, service_types)
                snapshot.services[service_name] = service_info
        except Exception as exc:
            logger.error("获取 ROS 服务列表失败: %s", exc)

        return snapshot

    def _detect_changes(self) -> Optional[Dict[str, Any]]:
        """Detect differences between consecutive snapshots and return a delta payload."""
        with self._lock:
            if not self._current_snapshot or not self._previous_snapshot:
                return None

            current = self._current_snapshot
            previous = self._previous_snapshot

            # Fast path: compare hashes first.
            if current.compute_hash() == previous.compute_hash():
                return None

            # Detailed comparison.
            delta = {
                "type": "delta",
                "snapshot_id": current.snapshot_id,
                "timestamp": current.timestamp,
            }

            # Node changes.
            old_nodes = set(previous.nodes.keys())
            new_nodes = set(current.nodes.keys())

            added_nodes = new_nodes - old_nodes
            removed_nodes = old_nodes - new_nodes

            if added_nodes:
                delta["added_nodes"] = [current.nodes[name].to_dict() for name in added_nodes]
            if removed_nodes:
                delta["removed_nodes"] = list(removed_nodes)

            # Topic changes.
            old_topics = set(previous.topics.keys())
            new_topics = set(current.topics.keys())

            added_topics = new_topics - old_topics
            removed_topics = old_topics - new_topics

            if added_topics:
                delta["added_topics"] = [current.topics[name].to_dict() for name in added_topics]
            if removed_topics:
                delta["removed_topics"] = list(removed_topics)

            # Detect updates to publisher or subscriber sets.
            updated_topics = []
            for topic_name in old_topics & new_topics:
                old_topic = previous.topics[topic_name]
                new_topic = current.topics[topic_name]

                if old_topic != new_topic:  # Uses __eq__ for set comparison.
                    updated_topics.append(new_topic.to_dict())

            if updated_topics:
                delta["updated_topics"] = updated_topics

            # Service changes.
            old_services = set(previous.services.keys())
            new_services = set(current.services.keys())

            added_services = new_services - old_services
            removed_services = old_services - new_services

            if added_services:
                delta["added_services"] = [current.services[name].to_dict() for name in added_services]
            if removed_services:
                delta["removed_services"] = list(removed_services)

            # Bail out when no changes are detected after the detailed pass.
            has_changes = any(k in delta for k in [
                "added_nodes", "removed_nodes",
                "added_topics", "removed_topics", "updated_topics",
                "added_services", "removed_services"
            ])

            return delta if has_changes else None

    def _build_full_data(self) -> Dict[str, Any]:
        """Build a full snapshot payload suitable for initial synchronisation."""
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
        """Broadcast a delta payload to every connected WebSocket client."""
        if not self._event_loop or not self._websocket_clients:
            return

        # Invoke the async broadcaster from the worker thread.
        asyncio.run_coroutine_threadsafe(
            self._async_broadcast(delta),
            self._event_loop
        )

    async def _async_broadcast(self, message: Dict[str, Any]):
        """Asynchronously deliver a message to each client, pruning dead sockets."""
        dead_clients = set()
        for client in self._websocket_clients:
            try:
                await client.send_json(message)
            except Exception as e:
                logger.debug(f"发送消息到客户端失败: {e}")
                dead_clients.add(client)

        # Remove any connections that failed during send.
        for client in dead_clients:
            self._websocket_clients.discard(client)

    def register_websocket(self, websocket):
        """Register a WebSocket client for subsequent broadcasts."""
        self._websocket_clients.add(websocket)
        logger.info(f"WebSocket客户端已注册，当前连接数: {len(self._websocket_clients)}")

    def unregister_websocket(self, websocket):
        """Unregister a WebSocket client when it disconnects."""
        self._websocket_clients.discard(websocket)
        logger.info(f"WebSocket客户端已注销，当前连接数: {len(self._websocket_clients)}")

    def get_full_snapshot(self) -> Dict[str, Any]:
        """Return the latest full snapshot for new WebSocket subscribers."""
        if not self._primed_event.is_set():
            self._primed_event.wait(timeout=2.0)

        return self._build_full_data()

    def get_topic_details(self, topic_name: str) -> Optional[Dict[str, Any]]:
        """Return a lazily fetched detail view for a specific topic."""
        with self._lock:
            if not self._current_snapshot:
                return None

            topic = self._current_snapshot.topics.get(topic_name)
            if not topic:
                return None

            return topic.to_dict(include_details=True)

    def add_bookmark(self, entity_type: str, entity_name: str) -> bool:
        """Persist a bookmark for the requested entity type."""
        if entity_type not in self._bookmarks:
            return False

        with self._lock:
            self._bookmarks[entity_type].add(entity_name)

        logger.info(f"添加标记: {entity_type}/{entity_name}")
        return True

    def remove_bookmark(self, entity_type: str, entity_name: str) -> bool:
        """Remove a bookmark for the requested entity type."""
        if entity_type not in self._bookmarks:
            return False

        with self._lock:
            self._bookmarks[entity_type].discard(entity_name)

        logger.info(f"移除标记: {entity_type}/{entity_name}")
        return True

    def get_bookmarks(self) -> Dict[str, List[str]]:
        """Return all stored bookmarks grouped by entity type."""
        with self._lock:
            return {
                "nodes": list(self._bookmarks["nodes"]),
                "topics": list(self._bookmarks["topics"]),
                "services": list(self._bookmarks["services"]),
            }

    def shutdown(self):
        """Stop the monitoring thread and clean up related resources."""
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=3.0)


# Global service instance.
ros_graph_monitor = ROSGraphMonitor()
