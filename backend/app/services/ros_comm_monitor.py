"""
ROS 通信监控服务
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    import rclpy
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from rclpy.qos import QoSProfile
    from rclpy.serialization import serialize_message
    from rosidl_runtime_py import set_message_fields  # type: ignore
    from rosidl_runtime_py.convert import message_to_ordereddict  # type: ignore
    from rosidl_runtime_py.utilities import get_message, get_service  # type: ignore
except ImportError:  # pragma: no cover - 在无 ROS 环境下触发
    rclpy = None  # type: ignore
    MultiThreadedExecutor = None  # type: ignore
    Node = None  # type: ignore
    QoSProfile = None  # type: ignore
    set_message_fields = None  # type: ignore
    message_to_ordereddict = None  # type: ignore
    get_message = None  # type: ignore
    get_service = None  # type: ignore

try:
    from ament_index_python.packages import (  # type: ignore
        get_package_share_directory,
        PackageNotFoundError,
    )
except ImportError:  # pragma: no cover
    get_package_share_directory = None  # type: ignore
    PackageNotFoundError = Exception  # type: ignore

from app.services.ros_graph_monitor import ros_graph_monitor

logger = logging.getLogger(__name__)


def _ros_time_to_iso(timestamp: Any) -> str:
    """将 ROS 时间戳转换为 ISO 字符串"""
    try:
        sec = getattr(timestamp, "sec", None) or getattr(timestamp, "seconds", 0)
        nanosec = getattr(timestamp, "nanosec", None) or getattr(timestamp, "nanoseconds", 0)
        wall_time = float(sec) + float(nanosec) / 1_000_000_000.0
    except Exception:
        wall_time = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(wall_time)) + f".{int((wall_time % 1)*1000):03d}Z"


def _wall_time_to_iso(wall_time: float) -> str:
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(wall_time))
    millis = int((wall_time % 1) * 1000)
    return f"{base}.{millis:03d}Z"


def _safe_message_to_dict(msg: Any) -> Any:
    if message_to_ordereddict is None:
        return str(msg)
    try:
        return message_to_ordereddict(msg)
    except Exception as exc:  # pragma: no cover
        logger.debug("消息转换失败: %s", exc)
        return str(msg)


def _safe_serialize(msg: Any) -> int:
    if serialize_message is None:
        return 0
    try:
        return len(serialize_message(msg))
    except Exception as exc:  # pragma: no cover
        logger.debug("序列化失败: %s", exc)
        return 0


@dataclass
class TopicSample:
    wall_time: float
    monotonic_time: float
    size_bytes: int
    frequency: float


class TopicSession:
    """话题监控会话"""

    def __init__(self, topic_name: str, msg_type_str: str, msg_type: Any, message_limit: int):
        self.topic_name = topic_name
        self.msg_type_str = msg_type_str
        self.msg_type = msg_type
        self.subscription: Optional[Any] = None

        self._messages: Deque[Dict[str, Any]] = deque(maxlen=message_limit)
        self._samples: Deque[TopicSample] = deque()

        self._last_receive_monotonic: Optional[float] = None
        self._last_frequency: float = 0.0
        self._lock = threading.Lock()
        self._last_access_monotonic: float = time.monotonic()

    def attach_subscription(self, subscription: Any) -> None:
        self.subscription = subscription

    def touch(self) -> None:
        with self._lock:
            self._last_access_monotonic = time.monotonic()

    def last_access(self) -> float:
        with self._lock:
            return self._last_access_monotonic

    def handle_message(self, message: Any) -> None:
        wall_time = time.time()
        monotonic_now = time.monotonic()

        seq = None
        header = getattr(message, "header", None)
        if header is not None:
            seq = getattr(header, "seq", None)
            if hasattr(header, "stamp"):
                timestamp_iso = _ros_time_to_iso(header.stamp)
            else:
                timestamp_iso = _wall_time_to_iso(wall_time)
        else:
            timestamp_iso = _wall_time_to_iso(wall_time)

        frequency = 0.0
        if self._last_receive_monotonic is not None:
            delta = monotonic_now - self._last_receive_monotonic
            if delta > 0:
                frequency = 1.0 / delta
        self._last_receive_monotonic = monotonic_now
        self._last_frequency = frequency

        message_dict = _safe_message_to_dict(message)
        size_bytes = _safe_serialize(message)

        record = {
            "timestamp": timestamp_iso,
            "seq": seq,
            "data": message_dict,
            "size_bytes": size_bytes,
        }

        sample = TopicSample(
            wall_time=wall_time,
            monotonic_time=monotonic_now,
            size_bytes=size_bytes,
            frequency=frequency,
        )

        with self._lock:
            self._messages.appendleft(record)
            self._samples.append(sample)
            self._last_access_monotonic = monotonic_now

    def get_messages(self, limit: int) -> List[Dict[str, Any]]:
        with self._lock:
            self._last_access_monotonic = time.monotonic()
            return list(self._messages)[:limit]

    def get_frequency(self) -> float:
        with self._lock:
            return self._last_frequency

    def prune_samples(self, window_seconds: int) -> None:
        threshold = time.monotonic() - window_seconds
        with self._lock:
            while self._samples and self._samples[0].monotonic_time < threshold:
                self._samples.popleft()

    def snapshot_samples(self, window_seconds: int) -> List[TopicSample]:
        self.prune_samples(window_seconds)
        with self._lock:
            return list(self._samples)


class ROSCommMonitor:
    """ROS 通信监控服务"""

    MESSAGE_CACHE_LIMIT = 50
    ANALYSIS_WINDOW_SECONDS = 1800
    SESSION_IDLE_SECONDS = 300
    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: Dict[str, TopicSession] = {}

        self._node: Optional[Node] = None
        self._executor: Optional[MultiThreadedExecutor] = None
        self._spin_thread: Optional[threading.Thread] = None
        self._cleanup_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._available = (
            rclpy is not None
            and MultiThreadedExecutor is not None
            and Node is not None
            and QoSProfile is not None
            and get_message is not None
        )

        if not self._available:
            logger.warning("未检测到 rclpy，通信监控功能将不可用")
            return

        try:
            if not rclpy.ok():
                rclpy.init(args=None)

            self._node = rclpy.create_node("rosdeck_comm_monitor")
            self._executor = MultiThreadedExecutor()
            self._executor.add_node(self._node)

            self._spin_thread = threading.Thread(target=self._spin_loop, daemon=True)
            self._spin_thread.start()

            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()

        except Exception as exc:  # pragma: no cover
            logger.exception("初始化 ROS 通信监控失败: %s", exc)
            self._available = False

    # ------------------------------------------------------------------ #
    # 公共 API
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        return self._available

    def list_topics(self) -> List[Dict[str, Any]]:
        snapshot = ros_graph_monitor.get_full_snapshot()
        topics = snapshot.get("topics", [])
        with self._lock:
            frequency_map = {name: session.get_frequency() for name, session in self._sessions.items()}
        results = []
        for topic in topics:
            freq = frequency_map.get(topic.get("name"), 0.0)
            results.append(
                {
                    "name": topic.get("name"),
                    "type": topic.get("type"),
                    "publishers": topic.get("publisher_count", 0),
                    "subscribers": topic.get("subscriber_count", 0),
                    "frequency": freq if freq > 0 else None,
                }
            )
        return results

    def get_messages(self, topic_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        session = self._ensure_session(topic_name)
        return session.get_messages(limit)

    def analyse_topic(self, topic_name: str, window_seconds: int) -> Dict[str, Any]:
        session = self._ensure_session(topic_name)
        samples = session.snapshot_samples(window_seconds)
        if not samples:
            return {
                "topic": topic_name,
                "window_seconds": window_seconds,
                "avg_frequency": 0.0,
                "max_frequency": 0.0,
                "avg_size": 0.0,
                "message_count": 0,
                "frequency_data": [],
                "size_distribution": [],
            }

        message_count = len(samples)
        total_size = sum(sample.size_bytes for sample in samples)
        avg_size = total_size / message_count if message_count else 0.0
        avg_frequency = message_count / window_seconds if window_seconds else 0.0
        max_frequency = max(sample.frequency for sample in samples if sample.frequency is not None) if samples else 0.0

        buckets: Dict[int, int] = {}
        for sample in samples:
            bucket_key = int(sample.wall_time)
            buckets[bucket_key] = buckets.get(bucket_key, 0) + 1
        frequency_data = [
            {
                "timestamp": _wall_time_to_iso(bucket + 0.0),
                "frequency": count,
            }
            for bucket, count in sorted(buckets.items())
        ]

        size_ranges: List[Tuple[str, int, Optional[int]]] = [
            ("<1KB", 0, 1024),
            ("1-5KB", 1024, 5 * 1024),
            ("5-20KB", 5 * 1024, 20 * 1024),
            (">=20KB", 20 * 1024, None),
        ]
        distribution = []
        for label, lower, upper in size_ranges:
            if upper is None:
                count = sum(1 for sample in samples if sample.size_bytes >= lower)
            else:
                count = sum(1 for sample in samples if lower <= sample.size_bytes < upper)
            distribution.append({"range": label, "count": count})

        return {
            "topic": topic_name,
            "window_seconds": window_seconds,
            "avg_frequency": avg_frequency,
            "max_frequency": max_frequency,
            "avg_size": avg_size,
            "message_count": message_count,
            "frequency_data": frequency_data,
            "size_distribution": distribution,
        }

    def list_services(self) -> List[Dict[str, Any]]:
        self._ensure_available()
        assert self._node is not None
        services = self._node.get_service_names_and_types()
        results = []
        for name, types in services:
            if not types:
                continue
            results.append({"name": name, "type": types[0]})
        results.sort(key=lambda item: item["name"])
        return results

    def call_service(self, service_name: str, params: Optional[Dict[str, Any]], timeout: float = 5.0) -> Dict[str, Any]:
        self._ensure_available()
        assert self._node is not None
        assert get_service is not None

        service_types = dict(self._node.get_service_names_and_types())
        type_candidates = service_types.get(service_name)
        if not type_candidates:
            raise ValueError(f"未找到服务 {service_name}")

        srv_type_str = type_candidates[0]
        try:
            srv_cls = get_service(srv_type_str)
        except (AttributeError, ModuleNotFoundError, ImportError) as exc:
            raise RuntimeError(f"加载服务类型 {srv_type_str} 失败: {exc}") from exc

        request = srv_cls.Request()
        if params and set_message_fields is not None:
            try:
                set_message_fields(request, params)
            except Exception as exc:
                raise ValueError(f"请求参数无法匹配服务字段: {exc}") from exc

        client = self._node.create_client(srv_cls, service_name)
        try:
            if not client.wait_for_service(timeout_sec=timeout):
                raise TimeoutError(f"等待服务 {service_name} 超时")

            future = client.call_async(request)
            deadline = time.time() + timeout
            while not future.done():
                if time.time() > deadline:
                    raise TimeoutError(f"调用服务 {service_name} 超时")
                time.sleep(0.05)

            response = future.result()
            result_payload = _safe_message_to_dict(response)
            return {
                "service": service_name,
                "type": srv_type_str,
                "result": result_payload,
            }
        finally:
            self._node.destroy_client(client)

    def list_message_types(self) -> List[Dict[str, Any]]:
        snapshot = ros_graph_monitor.get_full_snapshot()
        topics = snapshot.get("topics", [])
        usage: Dict[str, int] = {}
        for topic in topics:
            msg_type = topic.get("type")
            if not msg_type:
                continue
            usage[msg_type] = usage.get(msg_type, 0) + 1

        types = []
        for msg_type, count in usage.items():
            package, short_name = self._split_message_type(msg_type)
            types.append(
                {
                    "name": msg_type,
                    "package": package,
                    "short_name": short_name,
                    "usage_count": count,
                    "is_custom": package not in {"std_msgs", "builtin_interfaces"},
                }
            )
        types.sort(key=lambda item: item["name"])
        return types

    def get_message_type_details(self, type_name: str) -> Dict[str, Any]:
        self._ensure_available()
        assert get_message is not None
        package, short_name = self._split_message_type(type_name)

        try:
            msg_cls = get_message(type_name)
        except (AttributeError, ModuleNotFoundError, ImportError) as exc:
            raise ValueError(f"无法加载消息类型 {type_name}: {exc}") from exc

        fields_dict = {}
        try:
            fields_dict = msg_cls.get_fields_and_field_types()
        except Exception:
            fields_dict = {}

        fields = [
            {"name": field_name, "type": field_type, "description": "-"}
            for field_name, field_type in fields_dict.items()
        ]

        definition = self._load_message_definition(package, short_name)

        usage_count = 0
        for entry in self.list_message_types():
            if entry["name"] == type_name:
                usage_count = entry.get("usage_count", 0)
                break

        return {
            "name": type_name,
            "package": package,
            "short_name": short_name,
            "usage_count": usage_count,
            "definition": definition,
            "fields": fields,
        }

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def _spin_loop(self) -> None:  # pragma: no cover - 后台线程
        assert self._executor is not None
        while not self._stop_event.is_set():
            try:
                self._executor.spin_once(timeout_sec=0.1)
            except Exception as exc:
                logger.debug("通信监控 spin_once 异常: %s", exc)

    def _cleanup_loop(self) -> None:  # pragma: no cover - 后台线程
        while not self._stop_event.is_set():
            time.sleep(self.CLEANUP_INTERVAL_SECONDS)
            self._cleanup_idle_sessions()

    def _cleanup_idle_sessions(self) -> None:
        if not self._available:
            return
        assert self._node is not None
        with self._lock:
            idle_threshold = time.monotonic() - self.SESSION_IDLE_SECONDS
            idle_topics = [name for name, session in self._sessions.items() if session.last_access() < idle_threshold]
            for topic_name in idle_topics:
                session = self._sessions.pop(topic_name, None)
                if session and session.subscription is not None:
                    try:
                        self._node.destroy_subscription(session.subscription)
                    except Exception as exc:  # pragma: no cover
                        logger.debug("销毁订阅 %s 失败: %s", topic_name, exc)

    def _ensure_session(self, topic_name: str) -> TopicSession:
        self._ensure_available()
        with self._lock:
            existing = self._sessions.get(topic_name)
            if existing:
                existing.touch()
                return existing

        topic_info = self._resolve_topic_info(topic_name)
        msg_type_str = topic_info.get("type")
        if not msg_type_str:
            raise ValueError(f"话题 {topic_name} 缺少消息类型信息")

        assert get_message is not None
        try:
            msg_cls = get_message(msg_type_str)
        except (AttributeError, ModuleNotFoundError, ImportError) as exc:
            raise RuntimeError(f"加载消息类型 {msg_type_str} 失败: {exc}") from exc

        session = TopicSession(topic_name, msg_type_str, msg_cls, self.MESSAGE_CACHE_LIMIT)
        callback = lambda msg, tn=topic_name: self._handle_topic_message(tn, msg)

        assert self._node is not None
        qos = QoSProfile(depth=10)
        subscription = self._node.create_subscription(msg_cls, topic_name, callback, qos)
        session.attach_subscription(subscription)

        with self._lock:
            self._sessions[topic_name] = session
        logger.info("Topic %s 开始监控 (type=%s)", topic_name, msg_type_str)
        return session

    def _handle_topic_message(self, topic_name: str, message: Any) -> None:
        with self._lock:
            session = self._sessions.get(topic_name)
        if not session:
            logger.debug("收到未注册话题 %s 的消息，忽略", topic_name)
            return
        try:
            session.handle_message(message)
        except Exception as exc:  # pragma: no cover
            logger.debug("处理话题 %s 消息失败: %s", topic_name, exc)

    def _resolve_topic_info(self, topic_name: str) -> Dict[str, Any]:
        snapshot = ros_graph_monitor.get_full_snapshot()
        for topic in snapshot.get("topics", []):
            if topic.get("name") == topic_name:
                return topic
        raise ValueError(f"未找到话题 {topic_name}")

    def _split_message_type(self, type_name: str) -> Tuple[str, str]:
        parts = type_name.split("/")
        if len(parts) >= 2:
            package = parts[0]
            short_name = parts[-1]
        else:
            package = type_name
            short_name = type_name
        return package, short_name

    def _load_message_definition(self, package: str, short_name: str) -> str:
        if get_package_share_directory is None:
            return "# 消息定义不可用：缺少 ament 索引"
        try:
            share_dir = Path(get_package_share_directory(package))
        except PackageNotFoundError:
            return "# 消息定义不可用：未找到软件包"

        candidates = [
            share_dir / "msg" / f"{short_name}.msg",
            share_dir / "msg" / f"{short_name.lower()}.msg",
        ]
        for candidate in candidates:
            if candidate.exists():
                try:
                    return candidate.read_text(encoding="utf-8")
                except Exception as exc:  # pragma: no cover
                    logger.debug("读取消息定义失败: %s", exc)
        return "# 消息定义未找到"

    def _ensure_available(self) -> None:
        if not self._available:
            raise RuntimeError("ROS 通信监控不可用：未检测到 rclpy")

    def shutdown(self) -> None:  # pragma: no cover - 测试或退出时调用
        self._stop_event.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=1.0)
        if self._executor:
            try:
                self._executor.shutdown(timeout=1.0)
            except Exception:
                pass
        if self._node:
            try:
                self._node.destroy_node()
            except Exception:
                pass


# 全局实例
ros_comm_monitor = ROSCommMonitor()
