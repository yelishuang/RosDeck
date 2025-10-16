"""
Manage ros2 launch files, discovery, and execution lifecycles.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_LAUNCH_DIRS = [
    Path(os.path.expanduser("~/rosdeck/launch")),
    Path(os.path.expanduser("~/.rosdeck/launch")),
]


@dataclass
class LaunchProcess:
    launch_id: str
    command: List[str]
    workdir: Optional[Path]
    process: subprocess.Popen
    stdout_path: Path
    stderr_path: Path
    start_time: float = field(default_factory=time.time)
    package: Optional[str] = None
    launch_file: Optional[str] = None
    parameters: Dict[str, str] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.process.poll() is None

    def stop(self, timeout: float = 10.0):
        if not self.is_active():
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("launch 进程终止超时，强制结束")
            self.process.kill()


class RosLaunchManager:
    """Handle ros2 launch discovery, execution, and log collection."""

    def __init__(self):
        self.launch_dirs = [path for path in DEFAULT_LAUNCH_DIRS if path.exists()]
        self.launch_dirs.append(Path("/opt/ros") / os.environ.get("ROS_DISTRO", "humble") / "share")

        self._lock = threading.Lock()
        self._launches: Dict[str, LaunchProcess] = {}

    # Launch discovery -------------------------------------------------------
    def list_launch_files(self, search_term: Optional[str] = None, include_global: bool = False) -> List[Dict[str, str]]:
        results = []
        for directory in self._iter_search_dirs(include_global=include_global):
            if not directory.exists():
                continue
            for path in directory.rglob("*.launch.py"):
                if search_term and search_term.lower() not in path.name.lower():
                    continue
                results.append({
                    "name": path.name,
                    "path": str(path),
                    "directory": str(path.parent),
                })
        results.sort(key=lambda item: item["name"])
        return results

    def _iter_search_dirs(self, include_global: bool):
        for directory in DEFAULT_LAUNCH_DIRS:
            yield directory
        if include_global:
            distro = os.environ.get("ROS_DISTRO", "humble")
            global_root = Path(f"/opt/ros/{distro}/share")
            if global_root.exists():
                yield global_root

    # Launch inspection ------------------------------------------------------
    def preview_arguments(self, package: Optional[str], launch_file: str) -> Dict[str, any]:
        """
        Inspect launch arguments by invoking `ros2 launch --show-args`.
        """
        if package:
            cmd = ["ros2", "launch", package, launch_file, "--show-args"]
        else:
            cmd = ["ros2", "launch", launch_file, "--show-args"]
        logger.info("预览 launch 参数: %s", " ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "获取 Launch 参数失败")
        return {"output": proc.stdout}

    # Launch execution -------------------------------------------------------
    def start_launch(
        self,
        package: Optional[str],
        launch_file: str,
        parameters: Optional[Dict[str, str]] = None,
        additional_args: Optional[List[str]] = None,
        workdir: Optional[str] = None
    ) -> LaunchProcess:
        launch_id = str(uuid.uuid4())
        command = ["ros2", "launch"]
        if package:
            command.extend([package, launch_file])
        else:
            command.append(launch_file)

        parameters = parameters or {}
        for key, value in parameters.items():
            command.append(f"{key}:={value}")

        if additional_args:
            command.extend(additional_args)

        stdout_path = self._log_dir() / f"{launch_id}_stdout.log"
        stderr_path = self._log_dir() / f"{launch_id}_stderr.log"
        stdout_file = stdout_path.open("w", encoding="utf-8")
        stderr_file = stderr_path.open("w", encoding="utf-8")

        logger.info("启动 launch: %s", " ".join(command))
        process = subprocess.Popen(
            command,
            cwd=workdir,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True
        )

        launch_proc = LaunchProcess(
            launch_id=launch_id,
            command=command,
            workdir=Path(workdir) if workdir else None,
            process=process,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            package=package,
            launch_file=launch_file,
            parameters=parameters,
        )
        with self._lock:
            self._launches[launch_id] = launch_proc
        return launch_proc

    def _log_dir(self) -> Path:
        path = Path(os.path.expanduser("~/rosdeck/launch_logs"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def stop_launch(self, launch_id: str):
        with self._lock:
            session = self._launches.get(launch_id)
        if not session:
            raise KeyError(f"未找到 Launch 任务: {launch_id}")
        session.stop()
        with self._lock:
            self._launches.pop(launch_id, None)

    def list_active_launches(self) -> List[Dict[str, any]]:
        items = []
        with self._lock:
            for launch_id, session in list(self._launches.items()):
                if not session.is_active():
                    session.process.wait(timeout=1.0)
                    self._launches.pop(launch_id, None)
                    continue
                items.append({
                    "launch_id": launch_id,
                    "command": session.command,
                    "start_time": session.start_time,
                    "package": session.package,
                    "launch_file": session.launch_file,
                    "parameters": session.parameters,
                    "cwd": str(session.workdir) if session.workdir else None,
                })
        return items

    def get_logs(self, launch_id: str, tail: int = 200) -> Dict[str, List[str]]:
        with self._lock:
            session = self._launches.get(launch_id)
        if not session:
            # Allow reading historical logs if the session no longer exists.
            stdout_log = self._log_dir() / f"{launch_id}_stdout.log"
            stderr_log = self._log_dir() / f"{launch_id}_stderr.log"
        else:
            stdout_log = session.stdout_path
            stderr_log = session.stderr_path

        def read_tail(path: Path) -> List[str]:
            if not path.exists():
                return []
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if tail:
                return lines[-tail:]
            return lines

        return {
            "stdout": read_tail(stdout_log),
            "stderr": read_tail(stderr_log),
            "stdout_path": str(stdout_log),
            "stderr_path": str(stderr_log),
        }


# Shared singleton instance.
ros_launch_manager = RosLaunchManager()
