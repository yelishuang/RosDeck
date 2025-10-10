"""
存储管理相关路由
"""
import csv
import io
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, validator, root_validator

from app.services.storage_monitor import storage_monitor
from app.services.storage_operations import (
    storage_operations,
    StorageOperationError,
)
from app.deps.csrf import csrf_protection
from app.deps.admin_auth import admin_auth, get_current_username

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/storage", tags=["存储管理"])


# ==================== 数据模型 ====================

class CleanupRequest(BaseModel):
    target: str = Field(..., description="清理目标")
    custom_path: Optional[str] = Field(None, description="自定义路径")
    dry_run: bool = Field(default=False, description="是否为预估模式")

    @validator("target")
    def validate_target(cls, value: str):
        allowed = {"tmp_dirs", "apt_cache", "user_cache", "ros_logs", "custom"}
        if value not in allowed:
            raise ValueError("不支持的清理目标")
        return value

    @validator("custom_path")
    def validate_custom_path(cls, value: Optional[str], values):
        if values.get("target") == "custom":
            if not value:
                raise ValueError("custom 目标必须提供路径")
            if len(value) > 512:
                raise ValueError("自定义路径过长")
        return value


class MountRequest(BaseModel):
    device: str = Field(..., description="设备路径, 例如 /dev/sdb1")
    mountpoint: Optional[str] = Field(None, description="挂载点路径")
    options: Optional[str] = Field(None, description="挂载选项")
    action: str = Field(..., description="mount 或 umount")

    @validator("action")
    def validate_action(cls, value: str):
        if value not in {"mount", "umount"}:
            raise ValueError("操作必须为 mount 或 umount")
        return value

    @validator("device")
    def validate_device(cls, value: str):
        if not value.startswith("/dev/") or len(value) > 64:
            raise ValueError("设备路径无效")
        return value

    @validator("mountpoint")
    def validate_mountpoint(cls, value: Optional[str]):
        if value and (not value.startswith("/") or len(value) > 128):
            raise ValueError("挂载点必须为绝对路径且长度需小于 128 字符")
        return value

    @root_validator
    def check_mountpoint_for_mount(cls, values):
        action = values.get("action")
        mountpoint = values.get("mountpoint")
        if action == "mount" and not mountpoint:
            raise ValueError("挂载操作需要提供挂载点")
        return values


class PartitionRequest(BaseModel):
    device: str = Field(..., description="设备路径, 例如 /dev/sdb")
    operation: str = Field(..., description="mkfs 或 wipefs")
    filesystem: Optional[str] = Field(None, description="mkfs 时的文件系统类型")
    label: Optional[str] = Field(None, description="mkfs 时的卷标")
    extra_args: Optional[str] = Field(None, description="额外参数")

    @validator("operation")
    def validate_operation(cls, value: str):
        if value not in {"mkfs", "wipefs"}:
            raise ValueError("不支持的分区操作")
        return value

    @validator("device")
    def validate_device(cls, value: str):
        if not value.startswith("/dev/") or len(value) > 64:
            raise ValueError("设备路径无效")
        return value

    @validator("label")
    def validate_label(cls, value: Optional[str]):
        if value and len(value) > 32:
            raise ValueError("卷标长度过长")
        return value

    @root_validator
    def check_filesystem(cls, values):
        operation = values.get("operation")
        filesystem = values.get("filesystem")
        if operation == "mkfs" and not filesystem:
            raise ValueError("mkfs 操作需要指定文件系统类型")
        return values


class SmartSelfTestRequest(BaseModel):
    device: str = Field(..., description="设备路径")
    mode: str = Field(..., description="自检模式")

    @validator("mode")
    def validate_mode(cls, value: str):
        allowed = {"short", "long", "conveyance"}
        if value not in allowed:
            raise ValueError("不支持的自检模式")
        return value

    @validator("device")
    def validate_device(cls, value: str):
        if not value.startswith("/dev/") or len(value) > 64:
            raise ValueError("设备路径无效")
        return value


# ==================== 帮助函数 ====================

def _build_csv(report_payload: dict) -> str:
    """将报表数据转换为 CSV 文本"""
    summary = report_payload.get("summary", {})
    partitions = report_payload.get("partitions", [])
    history = summary.get("history", [])

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["Report Generated", report_payload.get("generated_at", "")])
    writer.writerow(["Total Bytes", summary.get("total_bytes", 0)])
    writer.writerow(["Used Bytes", summary.get("used_bytes", 0)])
    writer.writerow(["Free Bytes", summary.get("free_bytes", 0)])
    writer.writerow(["Usage Percent", summary.get("usage_percent", 0.0)])
    writer.writerow([])

    writer.writerow(["History"])
    writer.writerow(["Timestamp", "Usage Percent", "Read Bytes/s", "Write Bytes/s"])
    for entry in history:
        writer.writerow([
            entry.get("timestamp", ""),
            entry.get("usage_percent", ""),
            entry.get("read_bytes_per_sec", ""),
            entry.get("write_bytes_per_sec", ""),
        ])
    writer.writerow([])

    writer.writerow(["Partitions"])
    writer.writerow([
        "Device",
        "Mountpoint",
        "Filesystem",
        "Total Bytes",
        "Used Bytes",
        "Free Bytes",
        "Used Percent",
        "Read Bytes/s",
        "Write Bytes/s",
        "Options",
    ])
    for part in partitions:
        writer.writerow([
            part.get("device", ""),
            part.get("mountpoint", ""),
            part.get("fstype", ""),
            part.get("total_bytes", ""),
            part.get("used_bytes", ""),
            part.get("free_bytes", ""),
            part.get("used_percent", ""),
            part.get("read_bytes_per_sec", ""),
            part.get("write_bytes_per_sec", ""),
            part.get("opts", ""),
        ])

    return buffer.getvalue()


# ==================== 路由实现 ====================

@router.get("/summary")
async def get_storage_summary():
    """
    返回磁盘使用总览与历史数据
    """
    try:
        summary = storage_monitor.get_summary()
        return summary
    except Exception as exc:
        logger.error("获取存储总览失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": "获取存储信息失败"})


@router.get("/partitions")
async def get_storage_partitions():
    """
    返回分区详细信息
    """
    try:
        return {"partitions": storage_monitor.get_partitions()}
    except Exception as exc:
        logger.error("获取分区信息失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": "获取分区信息失败"})


@router.get("/report")
async def export_storage_report(
    format: str = Query(default="json", regex="^(json|csv)$")
):
    """
    导出磁盘报告
    """
    try:
        payload = storage_monitor.build_report_payload()
        if format == "json":
            return payload

        csv_text = _build_csv(payload)
        return Response(
            content=csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=storage-report.csv"},
        )
    except Exception as exc:
        logger.error("导出存储报告失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": "导出报告失败"})


@router.post(
    "/cleanup",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin),
    ],
)
async def cleanup_storage(
    body: CleanupRequest,
    username: str = Depends(get_current_username),
):
    """
    管理员执行清理任务
    """
    try:
        result = storage_operations.cleanup(
            target=body.target,
            custom_path=body.custom_path,
            dry_run=body.dry_run,
            actor=username,
        )
        return result
    except StorageOperationError as exc:
        detail = {"message": str(exc)}
        if getattr(exc, "log_id", None):
            detail["log_id"] = exc.log_id
        raise HTTPException(status_code=400, detail=detail)


@router.post(
    "/mount",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin),
    ],
)
async def mount_action(
    body: MountRequest,
    username: str = Depends(get_current_username),
):
    """
    管理员挂载/卸载设备
    """
    try:
        return storage_operations.mount_action(
            device=body.device,
            mountpoint=body.mountpoint or "",
            options=body.options,
            action=body.action,
            actor=username,
        )
    except StorageOperationError as exc:
        detail = {"message": str(exc)}
        if getattr(exc, "log_id", None):
            detail["log_id"] = exc.log_id
        raise HTTPException(status_code=400, detail=detail)


@router.post(
    "/partition",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin),
    ],
)
async def partition_action(
    body: PartitionRequest,
    username: str = Depends(get_current_username),
):
    """
    管理员执行分区/格式化操作
    """
    try:
        return storage_operations.partition_action(
            device=body.device,
            operation=body.operation,
            filesystem=body.filesystem,
            label=body.label,
            extra_args=body.extra_args,
            actor=username,
        )
    except StorageOperationError as exc:
        detail = {"message": str(exc)}
        if getattr(exc, "log_id", None):
            detail["log_id"] = exc.log_id
        raise HTTPException(status_code=400, detail=detail)


@router.post(
    "/smart-selftest",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin),
    ],
)
async def smart_selftest(
    body: SmartSelfTestRequest,
    username: str = Depends(get_current_username),
):
    """
    管理员执行 SMART 自检
    """
    try:
        return storage_operations.smart_selftest(
            device=body.device,
            mode=body.mode,
            actor=username,
        )
    except StorageOperationError as exc:
        detail = {"message": str(exc)}
        if getattr(exc, "log_id", None):
            detail["log_id"] = exc.log_id
        raise HTTPException(status_code=400, detail=detail)


@router.get(
    "/smart-report",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin),
    ],
)
async def smart_report(
    device: str = Query(..., description="设备路径, 例如 /dev/sda"),
    username: str = Depends(get_current_username),
):
    """
    管理员获取 SMART 报告
    """
    try:
        return storage_operations.smart_report(device=device, actor=username)
    except StorageOperationError as exc:
        detail = {"message": str(exc)}
        if getattr(exc, "log_id", None):
            detail["log_id"] = exc.log_id
        raise HTTPException(status_code=400, detail=detail)


@router.get(
    "/operations",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin),
    ],
)
async def list_operations():
    """
    获取近期管理员操作日志
    """
    return {"operations": storage_operations.list_logs(limit=50)}
