"""
rosbag2 export helpers supporting JSON and CSV output.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

try:
    import rosbag2_py  # type: ignore
    from rclpy.serialization import deserialize_message  # type: ignore
    from rosidl_runtime_py.utilities import get_message  # type: ignore
    from rosidl_runtime_py import message_to_ordereddict  # type: ignore
except ImportError:  # pragma: no cover
    rosbag2_py = None  # type: ignore

logger = logging.getLogger(__name__)


class RosbagExporterUnavailable(RuntimeError):
    pass


def _require_dependencies():
    if rosbag2_py is None:
        raise RosbagExporterUnavailable("rosbag2_py 或相关依赖缺失，无法执行导出")


def _normalize_topics(requested: Optional[Sequence[str]], available: Iterable[str]) -> List[str]:
    if not requested:
        return list(available)
    available_set = set(available)
    filtered = [topic for topic in requested if topic in available_set]
    if not filtered:
        raise ValueError("请求导出的主题不存在于 bag 文件中")
    return filtered


def _to_iso(timestamp_ns: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)
    return dt.isoformat()


def export_bag(
    bag_path: Path,
    output_format: str,
    topics: Optional[Sequence[str]] = None,
    start_time_ns: Optional[int] = None,
    end_time_ns: Optional[int] = None,
) -> Path:
    """
    Export bag data as CSV or JSON within the `exports` subdirectory.

    Args:
        bag_path: Path to the rosbag directory.
        output_format: Either "json" or "csv".
        topics: Optional list of topic names to include.
        start_time_ns: Optional inclusive start timestamp in nanoseconds.
        end_time_ns: Optional inclusive end timestamp in nanoseconds.
    """
    _require_dependencies()

    storage_options = rosbag2_py.StorageOptions(uri=str(bag_path), storage_id="sqlite3")
    converter_options = rosbag2_py.ConverterOptions(
        input_serialization_format="",
        output_serialization_format=""
    )

    reader = rosbag2_py.SequentialReader()
    reader.open(storage_options, converter_options)

    topics_meta = reader.get_all_topics_and_types()
    type_map = {meta.name: meta.type for meta in topics_meta}

    selected_topics = _normalize_topics(topics, type_map.keys())

    message_types: Dict[str, Any] = {}
    for topic in selected_topics:
        msg_type = type_map[topic]
        message_types[topic] = get_message(msg_type)

    output_dir = bag_path / "exports"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if output_format.lower() == "json":
        output_path = output_dir / f"export_{timestamp}.json"
    elif output_format.lower() == "csv":
        output_path = output_dir / f"export_{timestamp}.csv"
    else:
        raise ValueError("不支持的导出格式，仅支持 CSV/JSON")

    messages = []
    try:
        while reader.has_next():
            topic, data, timestamp_ns = reader.read_next()
            if topic not in selected_topics:
                continue
            if start_time_ns and timestamp_ns < start_time_ns:
                continue
            if end_time_ns and timestamp_ns > end_time_ns:
                continue
            msg_type = message_types.get(topic)
            if msg_type is None:
                continue
            msg = deserialize_message(data, msg_type)
            payload = message_to_ordereddict(msg)
            messages.append({
                "topic": topic,
                "timestamp": timestamp_ns,
                "timestamp_iso": _to_iso(timestamp_ns),
                "data": payload
            })
    finally:
        del reader

    if output_format.lower() == "json":
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(messages, fh, ensure_ascii=False, indent=2)
    else:
        with output_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["topic", "timestamp_ns", "timestamp_iso", "data_json"])
            for item in messages:
                writer.writerow([
                    item["topic"],
                    item["timestamp"],
                    item["timestamp_iso"],
                    json.dumps(item["data"], ensure_ascii=False)
                ])

    return output_path
