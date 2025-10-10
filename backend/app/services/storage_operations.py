"""
存储管理操作服务
提供目录清理、挂载/卸载、分区格式化以及 SMART 功能封装
"""
import os
import shutil
import subprocess
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Tuple
from collections import deque

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expand_path(path: str) -> Path:
    return Path(os.path.expanduser(path)).resolve()


class StorageOperationError(Exception):
    """存储操作异常"""

    def __init__(self, message: str, log_id: Optional[str] = None):
        super().__init__(message)
        self.log_id = log_id


class StorageOperations:
    """提供管理员存储操作能力"""

    CLEANUP_TARGETS: Dict[str, List[str]] = {
        "tmp_dirs": ["/tmp", "/var/tmp"],
        "apt_cache": ["/var/cache/apt"],
        "user_cache": ["~/.cache"],
        "ros_logs": ["~/.ros/log"],
    }

    SUPPORTED_FILESYSTEMS = {"ext4", "ext3", "ext2", "xfs", "vfat", "btrfs"}
    PERMITTED_EXTRA_ARGS_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789=-_/.,")

    def __init__(self, log_size: int = 100):
        self._lock = threading.Lock()
        self._operation_logs: Deque[Dict[str, object]] = deque(maxlen=log_size)

    def _record_log(self, *, actor: str, action: str, status: str, detail: Dict[str, object], message: str) -> str:
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "actor": actor,
            "action": action,
            "status": status,
            "detail": detail,
            "message": message,
        }
        with self._lock:
            self._operation_logs.appendleft(entry)
        logger.info("Storage operation %s %s by %s: %s", action, status, actor, message)
        return entry["id"]

    def list_logs(self, limit: int = 50) -> List[Dict[str, object]]:
        with self._lock:
            entries = list(self._operation_logs)
        return entries[:limit]

    # -------------------------- 清理操作 --------------------------

    def _iter_paths_for_target(self, target: str, custom_path: Optional[str]) -> Iterable[Path]:
        if target == "custom":
            if not custom_path:
                raise StorageOperationError("custom 模式需要提供路径")
            path_obj = _expand_path(custom_path)
            self._validate_safe_path(path_obj)
            return [path_obj]

        paths = self.CLEANUP_TARGETS.get(target)
        if not paths:
            raise StorageOperationError("未知的清理目标")

        resolved = []
        for raw in paths:
            path_obj = _expand_path(raw)
            if path_obj.exists():
                resolved.append(path_obj)
        return resolved

    @staticmethod
    def _validate_safe_path(path_obj: Path):
        if not path_obj.is_absolute():
            raise StorageOperationError("路径必须为绝对路径")
        parts = path_obj.parts
        if len(parts) <= 2:
            raise StorageOperationError("禁止操作根目录或顶级目录")

    def _directory_size(self, path_obj: Path) -> int:
        total = 0
        if path_obj.is_file():
            return path_obj.stat().st_size

        for root, dirs, files in os.walk(path_obj, onerror=lambda _: None):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except FileNotFoundError:
                    continue
        return total

    def _cleanup_path(self, path_obj: Path):
        if not path_obj.exists():
            return

        if path_obj.is_file() or path_obj.is_symlink():
            path_obj.unlink(missing_ok=True)
            return

        for child in path_obj.iterdir():
            try:
                if child.is_symlink():
                    child.unlink(missing_ok=True)
                elif child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("删除路径 %s 失败: %s", child, exc)

    def cleanup(self, *, target: str, custom_path: Optional[str], dry_run: bool, actor: str) -> Dict[str, object]:
        detail = {"target": target, "custom_path": custom_path, "dry_run": dry_run}
        try:
            paths = list(self._iter_paths_for_target(target, custom_path))
            if not paths:
                raise StorageOperationError("目标路径不存在或不可访问")

            bytes_to_free = sum(self._directory_size(path) for path in paths)

            if dry_run:
                log_id = self._record_log(actor=actor, action="cleanup",
                                          status="dry-run", detail=detail,
                                          message=f"预估清理空间 {bytes_to_free} 字节")
                return {"status": "dry_run", "freed_bytes": bytes_to_free, "log_id": log_id}

            for path in paths:
                self._cleanup_path(path)

            log_id = self._record_log(actor=actor, action="cleanup",
                                      status="success", detail=detail,
                                      message=f"清理完成, 预估释放 {bytes_to_free} 字节")

            return {"status": "completed", "freed_bytes": bytes_to_free, "log_id": log_id}
        except StorageOperationError as exc:
            log_id = self._record_log(actor=actor, action="cleanup",
                                      status="failed", detail=detail, message=str(exc))
            exc.log_id = log_id
            raise
        except Exception as exc:
            log_id = self._record_log(actor=actor, action="cleanup",
                                      status="failed", detail=detail, message=str(exc))
            raise StorageOperationError(f"清理失败: {exc}", log_id=log_id) from exc

    # -------------------------- 挂载操作 --------------------------

    @staticmethod
    def _validate_device_path(device: str):
        if not device.startswith("/dev/"):
            raise StorageOperationError("设备路径必须位于 /dev 下")
        allowed_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/_-")
        if not all(ch in allowed_chars for ch in device):
            raise StorageOperationError("设备路径包含非法字符")

    @staticmethod
    def _validate_mountpoint(mountpoint: str):
        if not mountpoint.startswith("/"):
            raise StorageOperationError("挂载点必须为绝对路径")
        if len(mountpoint) < 2:
            raise StorageOperationError("挂载点无效")

    def mount_action(self, *, device: str, mountpoint: str, options: Optional[str], action: str, actor: str) -> Dict[str, object]:
        detail = {"device": device, "mountpoint": mountpoint, "options": options, "action": action}
        try:
            self._validate_device_path(device)
            if action not in {"mount", "umount"}:
                raise StorageOperationError("不支持的操作")

            if action == "mount":
                self._validate_mountpoint(mountpoint)
                mount_path = Path(mountpoint)
                if not mount_path.exists():
                    mount_path.mkdir(parents=True, exist_ok=True)

                cmd = ["mount"]
                if options:
                    cmd.extend(["-o", options])
                cmd.extend([device, mountpoint])
            else:
                # 默认优先使用挂载点卸载
                target = mountpoint or device
                if not target:
                    raise StorageOperationError("卸载操作需要挂载点或设备")
                cmd = ["umount", target]

            logger.info("执行挂载命令: %s", " ".join(cmd))
            subprocess.run(cmd, check=True, timeout=60)

            log_id = self._record_log(actor=actor, action="mount",
                                      status="success", detail=detail,
                                      message=f"{action} 操作执行成功")
            return {"status": "completed", "log_id": log_id}
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log_id = self._record_log(actor=actor, action="mount",
                                      status="failed", detail=detail,
                                      message=str(exc))
            raise StorageOperationError(f"{action} 操作失败: {exc}", log_id=log_id) from exc
        except StorageOperationError:
            raise
        except Exception as exc:
            log_id = self._record_log(actor=actor, action="mount",
                                      status="failed", detail=detail,
                                      message=str(exc))
            raise StorageOperationError(f"{action} 操作失败: {exc}", log_id=log_id) from exc

    # -------------------------- 分区/格式化 --------------------------

    def _build_mkfs_command(self, filesystem: str, device: str, label: Optional[str], extra_args: Optional[str]) -> List[str]:
        if filesystem not in self.SUPPORTED_FILESYSTEMS:
            raise StorageOperationError("不支持的文件系统类型")

        cmd: List[str]
        if filesystem in {"ext4", "ext3", "ext2"}:
            cmd = [f"mkfs.{filesystem}", "-F", device]
        elif filesystem == "xfs":
            cmd = ["mkfs.xfs", "-f", device]
        elif filesystem == "vfat":
            cmd = ["mkfs.vfat", device]
        elif filesystem == "btrfs":
            cmd = ["mkfs.btrfs", "-f", device]
        else:
            cmd = [f"mkfs.{filesystem}", device]

        if label:
            if filesystem in {"vfat"}:
                cmd.extend(["-n", label])
            else:
                cmd.extend(["-L", label])

        if extra_args:
            extra_tokens = extra_args.split()
            for token in extra_tokens:
                if not set(token).issubset(self.PERMITTED_EXTRA_ARGS_CHARS):
                    raise StorageOperationError("额外参数包含非法字符")
            cmd.extend(extra_tokens)

        return cmd

    def partition_action(
        self,
        *,
        device: str,
        operation: str,
        filesystem: Optional[str],
        label: Optional[str],
        extra_args: Optional[str],
        actor: str
    ) -> Dict[str, object]:
        detail = {
            "device": device,
            "operation": operation,
            "filesystem": filesystem,
            "label": label,
            "extra_args": extra_args,
        }
        try:
            self._validate_device_path(device)

            if operation == "mkfs":
                if not filesystem:
                    raise StorageOperationError("mkfs 需要指定文件系统类型")
                cmd = self._build_mkfs_command(filesystem, device, label, extra_args)
            elif operation == "wipefs":
                cmd = ["wipefs", "-a", device]
            else:
                raise StorageOperationError("不支持的分区操作")

            logger.info("执行分区命令: %s", " ".join(cmd))
            subprocess.run(cmd, check=True, timeout=300)

            log_id = self._record_log(actor=actor, action="partition",
                                      status="success", detail=detail,
                                      message=f"{operation} 操作执行成功")
            return {"status": "completed", "log_id": log_id}
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log_id = self._record_log(actor=actor, action="partition",
                                      status="failed", detail=detail,
                                      message=str(exc))
            raise StorageOperationError(f"{operation} 操作失败: {exc}", log_id=log_id) from exc
        except StorageOperationError as exc:
            log_id = self._record_log(actor=actor, action="partition",
                                      status="failed", detail=detail,
                                      message=str(exc))
            exc.log_id = log_id
            raise
        except Exception as exc:
            log_id = self._record_log(actor=actor, action="partition",
                                      status="failed", detail=detail,
                                      message=str(exc))
            raise StorageOperationError(f"{operation} 操作失败: {exc}", log_id=log_id) from exc

    # -------------------------- SMART 功能 --------------------------

    @staticmethod
    def _run_smartctl(args: List[str]) -> subprocess.CompletedProcess:
        cmd = ["smartctl"] + args
        logger.info("执行 smartctl: %s", " ".join(cmd))
        return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)

    def smart_selftest(self, *, device: str, mode: str, actor: str) -> Dict[str, object]:
        detail = {"device": device, "mode": mode}
        try:
            self._validate_device_path(device)
            if mode not in {"short", "long", "conveyance"}:
                raise StorageOperationError("不支持的自检模式")

            result = self._run_smartctl(["-t", mode, device])

            estimated_minutes = None
            for line in result.stdout.splitlines():
                line = line.strip()
                if "Please wait" in line and "minutes" in line:
                    parts = [part for part in line.split() if part.isdigit()]
                    if parts:
                        estimated_minutes = int(parts[0])
                        break

            log_id = self._record_log(actor=actor, action="smart-selftest",
                                      status="started", detail=detail,
                                      message="SMART 自检已启动")

            return {
                "status": "started",
                "estimated_minutes": estimated_minutes,
                "log_id": log_id,
            }
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log_id = self._record_log(actor=actor, action="smart-selftest",
                                      status="failed", detail=detail,
                                      message=str(exc))
            raise StorageOperationError(f"SMART 自检启动失败: {exc}", log_id=log_id) from exc
        except StorageOperationError as exc:
            log_id = self._record_log(actor=actor, action="smart-selftest",
                                      status="failed", detail=detail,
                                      message=str(exc))
            exc.log_id = log_id
            raise
        except Exception as exc:
            log_id = self._record_log(actor=actor, action="smart-selftest",
                                      status="failed", detail=detail,
                                      message=str(exc))
            raise StorageOperationError(f"SMART 自检启动失败: {exc}", log_id=log_id) from exc

    def smart_report(self, *, device: str, actor: str) -> Dict[str, object]:
        detail = {"device": device}
        try:
            self._validate_device_path(device)
            result = self._run_smartctl(["--json", "-H", "-A", "-l", "selftest,selective", device])

            try:
                import json
                payload = json.loads(result.stdout)
            except ValueError as exc:
                raise StorageOperationError(f"解析 smartctl 输出失败: {exc}") from exc

            report = {
                "device": device,
                "model": payload.get("model_family") or payload.get("model_name"),
                "serial": payload.get("serial_number"),
                "firmware": payload.get("firmware_version"),
                "temperature": payload.get("temperature", {}).get("current"),
                "overall_health": None,
                "attributes": [],
                "selftest": [],
                "generated_at": _now_iso(),
            }

            if "smart_status" in payload:
                status = payload["smart_status"]
                passed = status.get("passed")
                if passed is True:
                    report["overall_health"] = "PASSED"
                elif passed is False:
                    report["overall_health"] = "FAILED"

            for attr in payload.get("ata_smart_attributes", {}).get("table", []):
                report["attributes"].append({
                    "id": attr.get("id"),
                    "name": attr.get("name"),
                    "value": attr.get("value"),
                    "worst": attr.get("worst"),
                    "threshold": attr.get("thresh"),
                    "raw": attr.get("raw", {}).get("value"),
                })

            for entry in payload.get("ata_smart_self_test_log", {}).get("standard", {}).get("table", []):
                report["selftest"].append({
                    "type": entry.get("type", {}).get("string"),
                    "status": entry.get("status", {}).get("string"),
                    "lifetime_hours": entry.get("life_time_hours"),
                    "timestamp": entry.get("timestamp"),
                })

            log_id = self._record_log(actor=actor, action="smart-report",
                                      status="success", detail=detail,
                                      message="SMART 报告获取成功")
            report["log_id"] = log_id
            return report
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            log_id = self._record_log(actor=actor, action="smart-report",
                                      status="failed", detail=detail,
                                      message=str(exc))
            raise StorageOperationError(f"获取 SMART 报告失败: {exc}", log_id=log_id) from exc
        except StorageOperationError as exc:
            log_id = self._record_log(actor=actor, action="smart-report",
                                      status="failed", detail=detail,
                                      message=str(exc))
            exc.log_id = log_id
            raise
        except Exception as exc:
            log_id = self._record_log(actor=actor, action="smart-report",
                                      status="failed", detail=detail,
                                      message=str(exc))
            raise StorageOperationError(f"获取 SMART 报告失败: {exc}", log_id=log_id) from exc


# 全局实例
storage_operations = StorageOperations()
