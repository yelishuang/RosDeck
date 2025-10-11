"""
rosbag2 录制/回放管理
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - 运行环境缺少 PyYAML
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _format_bytes(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    idx = 0
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.2f} {units[idx]}"


def _load_metadata(path: Path) -> Dict[str, Any]:
    if yaml is None:
        return {}
    metadata_file = path / "metadata.yaml"
    if not metadata_file.exists():
        return {}
    try:
        with metadata_file.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
            return data
    except Exception as exc:
        logger.debug("解析元数据失败 %s: %s", metadata_file, exc)
        return {}


@dataclasses.dataclass
class RecordingSession:
    recording_id: str
    topics: List[str]
    output_path: Path
    process: subprocess.Popen
    start_time: float
    duration_limit: Optional[int]
    size_limit_mb: Optional[int]
    preset_name: Optional[str]
    log_path: Path

    def is_active(self) -> bool:
        return self.process.poll() is None

    def stop(self):
        if not self.is_active():
            return
        try:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                logger.warning("录制停止超时，尝试终止进程")
                self.process.terminate()
        except ProcessLookupError:
            pass


class RosbagManager:
    """管理 rosbag2 录制、列表、删除"""

    PRESET_FILE = Path(os.path.expanduser("~/.config/rosdeck/recording_presets.json"))

    def __init__(self, base_directory: Optional[Path] = None):
        if base_directory:
            self.base_dir = base_directory
        else:
            self.base_dir = Path(os.path.expanduser("~/rosdeck/rosbags"))
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._recordings: Dict[str, RecordingSession] = {}
        self._playbacks: Dict[str, "RosbagManager.PlaybackSession"] = {}

        self._presets = self._load_presets()

    # Presets -----------------------------------------------------------------
    def _load_presets(self) -> Dict[str, Dict[str, Any]]:
        if self.PRESET_FILE.exists():
            try:
                with self.PRESET_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    logger.info("加载录制预设: %d 个", len(data))
                    return data
            except Exception as exc:
                logger.warning("加载录制预设失败: %s", exc)
        # 内置默认模板
        return {
            "default_system": {
                "label": "系统关键主题",
                "topics": ["/tf", "/tf_static", "/rosout"],
                "description": "包含 TF 与系统日志主题"
            },
            "navigation_basics": {
                "label": "导航基础",
                "topics": ["/map", "/cmd_vel", "/odometry/filtered"],
                "description": "适用于常规导航场景"
            }
        }

    def list_presets(self) -> List[Dict[str, Any]]:
        presets = []
        for preset_id, info in self._presets.items():
            item = {
                "id": preset_id,
                "label": info.get("label", preset_id),
                "topics": info.get("topics", []),
                "description": info.get("description", ""),
            }
            presets.append(item)
        presets.sort(key=lambda x: x["label"])
        return presets

    def add_preset(self, preset_id: str, topics: List[str], label: Optional[str] = None, description: str = ""):
        self._presets[preset_id] = {
            "label": label or preset_id,
            "topics": topics,
            "description": description,
        }
        self._persist_presets()

    def remove_preset(self, preset_id: str):
        if preset_id in self._presets:
            del self._presets[preset_id]
            self._persist_presets()

    def _persist_presets(self):
        self.PRESET_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.PRESET_FILE.open("w", encoding="utf-8") as fh:
                json.dump(self._presets, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning("保存录制预设失败: %s", exc)

    # Recording ----------------------------------------------------------------
    def start_recording(
        self,
        topics: List[str],
        preset_name: Optional[str] = None,
        duration_limit: Optional[int] = None,
        size_limit_mb: Optional[int] = None,
        storage_id: str = "sqlite3"
    ) -> RecordingSession:
        if not topics:
            raise ValueError("至少选择一个话题进行录制")

        bag_name = f"record_{time.strftime('%Y%m%d_%H%M%S')}"
        bag_path = self.base_dir / bag_name
        bag_path.mkdir(parents=True, exist_ok=False)

        cmd = ["ros2", "bag", "record", "-o", str(bag_path), "-s", storage_id]

        if duration_limit and duration_limit > 0:
            cmd.extend(["--duration", str(duration_limit)])
        if size_limit_mb and size_limit_mb > 0:
            cmd.extend(["--max-bag-size", str(size_limit_mb * 1024 * 1024)])

        cmd.extend(topics)

        log_path = bag_path / "rosbag_record.log"
        log_file = log_path.open("w", encoding="utf-8")

        logger.info("启动 rosbag 录制: %s", " ".join(cmd))
        process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)
        log_file.close()
        log_file.close()

        recording_id = str(uuid.uuid4())
        session = RecordingSession(
            recording_id=recording_id,
            topics=topics,
            output_path=bag_path,
            process=process,
            start_time=_now(),
            duration_limit=duration_limit,
            size_limit_mb=size_limit_mb,
            preset_name=preset_name,
            log_path=log_path,
        )

        with self._lock:
            self._recordings[recording_id] = session

        return session

    def list_active_recordings(self) -> List[Dict[str, Any]]:
        results = []
        with self._lock:
            for recording_id, session in list(self._recordings.items()):
                if not session.is_active():
                    # 自动清理已结束
                    session.process.wait(timeout=1.0)
                    del self._recordings[recording_id]
                    continue
                info = self._build_recording_status(session)
                results.append(info)
        return results

    def _build_recording_status(self, session: RecordingSession) -> Dict[str, Any]:
        elapsed = _now() - session.start_time
        size_bytes = self._compute_directory_size(session.output_path)
        metadata = _load_metadata(session.output_path)
        message_count = None
        if metadata:
            # metadata.yaml 的结构: metadata["topics_with_message_count"]
            try:
                total = sum(item.get("message_count", 0) for item in metadata.get("topics_with_message_count", []))
                message_count = int(total)
            except Exception:
                message_count = None

        return {
            "recording_id": session.recording_id,
            "topics": session.topics,
            "output_path": str(session.output_path),
            "preset_name": session.preset_name,
            "start_time": session.start_time,
            "elapsed_seconds": elapsed,
            "size_bytes": size_bytes,
            "size_text": _format_bytes(size_bytes),
            "message_count": message_count,
            "duration_limit": session.duration_limit,
            "size_limit_mb": session.size_limit_mb,
        }

    def stop_recording(self, recording_id: str) -> Dict[str, Any]:
        with self._lock:
            session = self._recordings.get(recording_id)

        if not session:
            raise KeyError(f"未找到录制任务: {recording_id}")

        session.stop()
        time.sleep(0.5)
        with self._lock:
            self._recordings.pop(recording_id, None)

        status = self._build_recording_status(session)
        status["active"] = False
        return status

    def stop_all(self) -> List[Dict[str, Any]]:
        results = []
        with self._lock:
            recording_ids = list(self._recordings.keys())
        for rec_id in recording_ids:
            try:
                results.append(self.stop_recording(rec_id))
            except Exception as exc:
                logger.error("停止录制失败 %s: %s", rec_id, exc)
        return results

    # Playback ----------------------------------------------------------------
    @dataclasses.dataclass
    class PlaybackSession:
        playback_id: str
        bag_path: Path
        process: subprocess.Popen
        start_time: float
        rate: float
        loop: bool
        topics: Optional[List[str]]
        log_path: Path

        def is_active(self) -> bool:
            return self.process.poll() is None

        def stop(self):
            if not self.is_active():
                return
            try:
                self.process.send_signal(signal.SIGINT)
                self.process.wait(timeout=10.0)
            except Exception:
                self.process.terminate()

    def start_playback(
        self,
        bag_name: str,
        rate: float = 1.0,
        loop: bool = False,
        topics: Optional[List[str]] = None
    ) -> "RosbagManager.PlaybackSession":
        bag_path = self.base_dir / bag_name
        if not bag_path.exists():
            raise FileNotFoundError("Bag 文件不存在")

        cmd = ["ros2", "bag", "play", str(bag_path), "--rate", str(rate)]
        if loop:
            cmd.append("--loop")
        if topics:
            for topic in topics:
                cmd.extend(["--topics", topic])

        log_path = bag_path / f"playback_{int(_now())}.log"
        log_file = log_path.open("w", encoding="utf-8")
        logger.info("启动 rosbag 回放: %s", " ".join(cmd))
        process = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, text=True)

        session = RosbagManager.PlaybackSession(
            playback_id=str(uuid.uuid4()),
            bag_path=bag_path,
            process=process,
            start_time=_now(),
            rate=rate,
            loop=loop,
            topics=topics,
            log_path=log_path,
        )
        with self._lock:
            self._playbacks[session.playback_id] = session
        return session

    def list_playbacks(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._playbacks.items())
        results = []
        for playback_id, session in items:
            if not session.is_active():
                session.process.wait(timeout=1.0)
                with self._lock:
                    self._playbacks.pop(playback_id, None)
                continue
            results.append({
                "playback_id": playback_id,
                "bag_name": session.bag_path.name,
                "start_time": session.start_time,
                "rate": session.rate,
                "loop": session.loop,
                "topics": session.topics,
            })
        return results

    def stop_playback(self, playback_id: str) -> Dict[str, Any]:
        with self._lock:
            session = self._playbacks.get(playback_id)
        if not session:
            raise KeyError("未找到回放任务")
        session.stop()
        with self._lock:
            self._playbacks.pop(playback_id, None)
        return {
            "playback_id": playback_id,
            "bag_name": session.bag_path.name,
            "stopped": True
        }

    # Bag files ---------------------------------------------------------------
    def list_bag_files(self) -> List[Dict[str, Any]]:
        bags = []
        for entry in sorted(self.base_dir.iterdir()):
            if not entry.is_dir():
                continue
            metadata = _load_metadata(entry)
            size_bytes = self._compute_directory_size(entry)
            item = {
                "name": entry.name,
                "path": str(entry),
                "size_bytes": size_bytes,
                "size_text": _format_bytes(size_bytes),
                "created_at": entry.stat().st_ctime,
                "metadata": metadata,
            }
            bags.append(item)
        return bags

    def delete_bag(self, bag_name: str):
        bag_path = self.base_dir / bag_name
        if not bag_path.exists():
            raise FileNotFoundError("Bag 文件不存在")
        if bag_path in (session.output_path for session in self._recordings.values()):
            raise RuntimeError("无法删除正在录制的 Bag")
        shutil.rmtree(bag_path)

    def get_bag_info(self, bag_name: str) -> Dict[str, Any]:
        bag_path = self.base_dir / bag_name
        if not bag_path.exists():
            raise FileNotFoundError("Bag 文件不存在")
        metadata = _load_metadata(bag_path)
        size_bytes = self._compute_directory_size(bag_path)
        return {
            "name": bag_name,
            "path": str(bag_path),
            "size_bytes": size_bytes,
            "size_text": _format_bytes(size_bytes),
            "metadata": metadata,
        }

    def archive_bag(self, bag_name: str, archive_format: str = "zip") -> Path:
        bag_path = self.base_dir / bag_name
        if not bag_path.exists():
            raise FileNotFoundError("Bag 文件不存在")
        archive_root = self.base_dir / "archives"
        archive_root.mkdir(exist_ok=True)
        archive_target = archive_root / f"{bag_name}"
        archive_path = shutil.make_archive(str(archive_target), archive_format, root_dir=bag_path)
        return Path(archive_path)

    # Utilities ---------------------------------------------------------------
    @staticmethod
    def _compute_directory_size(path: Path) -> int:
        total = 0
        for root, _, files in os.walk(path):
            for fname in files:
                fpath = Path(root) / fname
                try:
                    total += fpath.stat().st_size
                except OSError:
                    continue
        return total


# 全局实例
rosbag_manager = RosbagManager()
