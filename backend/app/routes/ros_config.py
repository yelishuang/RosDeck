"""
ROS configuration and data acquisition endpoints.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, validator

from app.deps.admin_auth import admin_auth
from app.services.ros_parameter_manager import ros_parameter_manager
from app.services.rosbag_exporter import RosbagExporterUnavailable, export_bag
from app.services.rosbag_manager import rosbag_manager
from app.services.ros_lifecycle_manager import node_lifecycle_manager
from app.services.ros_launch_manager import ros_launch_manager
from app.services.ros_graph_monitor import ros_graph_monitor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ros/config", tags=["ROS Config"])


# ---------------------------------------------------------------------------
# Parameter management
# ---------------------------------------------------------------------------


@router.get("/parameters/nodes")
async def list_parameter_nodes():
    nodes = ros_parameter_manager.list_nodes()
    return {"nodes": nodes, "available": ros_parameter_manager.is_available()}


@router.get("/parameters/details")
async def get_node_parameters(node: str = Query(..., description="节点完整名称，例如 /my_node")):
    try:
        data = ros_parameter_manager.get_node_parameters(node)
        return {"node": node, "flat": data["flat"], "tree": data["tree"]}
    except Exception as exc:
        logger.error("获取节点参数失败 %s: %s", node, exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.get("/parameters/tree")
async def get_parameter_tree():
    try:
        return {"tree": ros_parameter_manager.get_tree_view()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.get("/parameters/events")
async def get_parameter_events(since: Optional[float] = Query(None, description="时间戳（秒）")):
    try:
        events = ros_parameter_manager.get_recent_events(since)
        serialised = []
        for event in events:
            serialised.append({
                "timestamp": event.timestamp,
                "node": event.node,
                "added": event.added,
                "changed": event.changed,
                "deleted": event.deleted,
            })
        return {"events": serialised}
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.get("/parameters/snapshot")
async def export_parameter_snapshot():
    try:
        snapshot = ros_parameter_manager.export_snapshot()
    except Exception as exc:
        logger.error("导出参数快照失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})

    content = json.dumps(snapshot, ensure_ascii=False, indent=2)
    filename = f"ros_parameters_{snapshot['generated_at'].replace(':', '')}.json"
    response = Response(content=content, media_type="application/json")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Recording management
# ---------------------------------------------------------------------------


class StartRecordingRequest(BaseModel):
    topics: List[str]
    preset_name: Optional[str] = None
    duration_limit: Optional[int] = Field(None, description="录制时长限制（秒）")
    size_limit_mb: Optional[int] = Field(None, description="文件大小限制（MB）")
    storage_id: Optional[str] = Field("sqlite3", description="rosbag 存储后端")

    @validator("topics")
    def validate_topics(cls, value):
        if not value:
            raise ValueError("至少选择一个话题")
        return value


@router.get("/recordings/presets")
async def list_recording_presets():
    presets = rosbag_manager.list_presets()
    return {"presets": presets}


@router.get("/recordings/active")
async def list_active_recordings():
    recordings = rosbag_manager.list_active_recordings()
    return {"recordings": recordings}


@router.post("/recordings/start", dependencies=[Depends(admin_auth.require_admin)])
async def start_recording(payload: StartRecordingRequest):
    try:
        session = rosbag_manager.start_recording(
            topics=payload.topics,
            preset_name=payload.preset_name,
            duration_limit=payload.duration_limit,
            size_limit_mb=payload.size_limit_mb,
            storage_id=payload.storage_id or "sqlite3"
        )
        status = rosbag_manager.list_active_recordings()
        return {"recording_id": session.recording_id, "status": status}
    except Exception as exc:
        logger.error("启动录制失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


class StopRecordingRequest(BaseModel):
    recording_id: str


@router.post("/recordings/stop", dependencies=[Depends(admin_auth.require_admin)])
async def stop_recording(payload: StopRecordingRequest):
    try:
        result = rosbag_manager.stop_recording(payload.recording_id)
        return {"result": result}
    except KeyError:
        raise HTTPException(status_code=404, detail={"message": "录制任务不存在"})
    except Exception as exc:
        logger.error("停止录制失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.post("/recordings/stop-all", dependencies=[Depends(admin_auth.require_admin)])
async def stop_all_recordings():
    try:
        results = rosbag_manager.stop_all()
        return {"results": results}
    except Exception as exc:
        logger.error("停止全部录制失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


# ---------------------------------------------------------------------------
# Bag file management
# ---------------------------------------------------------------------------


@router.get("/bags")
async def list_bags():
    bags = rosbag_manager.list_bag_files()
    return {"bags": bags}


@router.get("/bags/{bag_name}/info")
async def get_bag_info(bag_name: str):
    try:
        info = rosbag_manager.get_bag_info(bag_name)
        return info
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"message": "Bag 文件不存在"})
    except Exception as exc:
        logger.error("获取 bag 信息失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


class BagExportRequest(BaseModel):
    format: str = Field(..., regex="^(csv|json)$")
    topics: Optional[List[str]] = None
    start_time_ns: Optional[int] = None
    end_time_ns: Optional[int] = None


@router.post("/bags/{bag_name}/export")
async def export_bag_data(bag_name: str, payload: BagExportRequest, admin=Depends(admin_auth.require_admin)):
    try:
        bag_path = Path(rosbag_manager.base_dir) / bag_name
        if not bag_path.exists():
            raise HTTPException(status_code=404, detail={"message": "Bag 文件不存在"})
        export_path = export_bag(
            bag_path=bag_path,
            output_format=payload.format,
            topics=payload.topics,
            start_time_ns=payload.start_time_ns,
            end_time_ns=payload.end_time_ns,
        )
        filename = export_path.name
        return FileResponse(export_path, filename=filename, media_type="application/octet-stream")
    except RosbagExporterUnavailable as exc:
        raise HTTPException(status_code=503, detail={"message": str(exc)})
    except Exception as exc:
        logger.error("导出 bag 数据失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.get("/bags/{bag_name}/download")
async def download_bag_archive(bag_name: str, admin=Depends(admin_auth.require_admin)):
    try:
        archive_path = rosbag_manager.archive_bag(bag_name)
        media_type = "application/zip" if archive_path.suffix == ".zip" else "application/octet-stream"
        return FileResponse(archive_path, filename=archive_path.name, media_type=media_type)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"message": "Bag 文件不存在"})
    except Exception as exc:
        logger.error("打包 bag 失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.delete("/bags/{bag_name}", dependencies=[Depends(admin_auth.require_admin)])
async def delete_bag(bag_name: str):
    try:
        rosbag_manager.delete_bag(bag_name)
        return {"deleted": bag_name}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"message": "Bag 文件不存在"})
    except Exception as exc:
        logger.error("删除 bag 失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


# ---------------------------------------------------------------------------
# Bag playback
# ---------------------------------------------------------------------------


class StartPlaybackRequest(BaseModel):
    bag_name: str
    rate: Optional[float] = 1.0
    loop: bool = False
    topics: Optional[List[str]] = None


@router.post("/playback/start", dependencies=[Depends(admin_auth.require_admin)])
async def start_playback(payload: StartPlaybackRequest):
    try:
        session = rosbag_manager.start_playback(
            bag_name=payload.bag_name,
            rate=payload.rate or 1.0,
            loop=payload.loop,
            topics=payload.topics
        )
        return {"playback_id": session.playback_id}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"message": "Bag 文件不存在"})
    except Exception as exc:
        logger.error("启动回放失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.get("/playback/active")
async def list_playbacks():
    sessions = rosbag_manager.list_playbacks()
    return {"playbacks": sessions}


class StopPlaybackRequest(BaseModel):
    playback_id: str


@router.post("/playback/stop", dependencies=[Depends(admin_auth.require_admin)])
async def stop_playback(payload: StopPlaybackRequest):
    try:
        result = rosbag_manager.stop_playback(payload.playback_id)
        return result
    except KeyError:
        raise HTTPException(status_code=404, detail={"message": "回放任务不存在"})
    except Exception as exc:
        logger.error("停止回放失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


# ---------------------------------------------------------------------------
# Node lifecycle management
# ---------------------------------------------------------------------------


@router.get("/lifecycle/nodes")
async def lifecycle_nodes():
    try:
        nodes = node_lifecycle_manager.list_nodes()
        data = [
            {
                "name": item.name,
                "namespace": item.namespace,
                "full_name": item.full_name,
                "is_lifecycle": item.is_lifecycle,
                "current_state": item.current_state,
                "available_states": item.available_states,
            }
            for item in nodes
        ]
        return {"nodes": data}
    except Exception as exc:
        logger.error("获取生命周期节点失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


class RestartNodeRequest(BaseModel):
    node: str


@router.post("/lifecycle/restart", dependencies=[Depends(admin_auth.require_admin)])
async def restart_lifecycle_node(payload: RestartNodeRequest):
    try:
        return node_lifecycle_manager.restart_node(payload.node)
    except Exception as exc:
        logger.error("重启节点失败 %s: %s", payload.node, exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.get("/lifecycle/logs")
async def get_lifecycle_logs(node: str = Query(..., description="节点名称"), limit: int = Query(200, ge=0, le=2000)):
    try:
        logs = node_lifecycle_manager.get_logs(node, line_limit=limit)
        return logs
    except Exception as exc:
        logger.error("获取节点日志失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.get("/lifecycle/startup-info")
async def get_startup_info(node: str = Query(..., description="节点名称")):
    try:
        info = node_lifecycle_manager.get_startup_info(node)
        return info
    except Exception as exc:
        logger.error("获取节点启动信息失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


# ---------------------------------------------------------------------------
# Launch file orchestration
# ---------------------------------------------------------------------------


@router.get("/launch/files")
async def list_launch_files(search: Optional[str] = None, include_global: bool = False):
    files = ros_launch_manager.list_launch_files(search_term=search, include_global=include_global)
    return {"files": files}


class LaunchPreviewRequest(BaseModel):
    package: Optional[str] = None
    launch_file: str


@router.post("/launch/preview")
async def preview_launch_args(payload: LaunchPreviewRequest):
    try:
        preview = ros_launch_manager.preview_arguments(payload.package, payload.launch_file)
        return preview
    except Exception as exc:
        logger.error("获取 Launch 参数失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


class LaunchStartRequest(BaseModel):
    package: Optional[str] = None
    launch_file: str
    parameters: Optional[Dict[str, str]] = None
    additional_args: Optional[List[str]] = None
    workdir: Optional[str] = None


@router.post("/launch/start", dependencies=[Depends(admin_auth.require_admin)])
async def start_launch(payload: LaunchStartRequest):
    try:
        session = ros_launch_manager.start_launch(
            package=payload.package,
            launch_file=payload.launch_file,
            parameters=payload.parameters,
            additional_args=payload.additional_args,
            workdir=payload.workdir
        )
        return {"launch_id": session.launch_id}
    except Exception as exc:
        logger.error("启动 Launch 失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


class LaunchStopRequest(BaseModel):
    launch_id: str


@router.post("/launch/stop", dependencies=[Depends(admin_auth.require_admin)])
async def stop_launch(payload: LaunchStopRequest):
    try:
        ros_launch_manager.stop_launch(payload.launch_id)
        return {"stopped": payload.launch_id}
    except KeyError:
        raise HTTPException(status_code=404, detail={"message": "未找到 Launch 任务"})
    except Exception as exc:
        logger.error("停止 Launch 失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


@router.get("/launch/active")
async def list_active_launches():
    sessions = ros_launch_manager.list_active_launches()
    return {"launches": sessions}


@router.get("/launch/logs")
async def launch_logs(launch_id: str = Query(...), tail: int = Query(200, ge=0, le=2000)):
    try:
        logs = ros_launch_manager.get_logs(launch_id, tail)
        return logs
    except Exception as exc:
        logger.error("读取 Launch 日志失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})


# ---------------------------------------------------------------------------
# Auxiliary resources
# ---------------------------------------------------------------------------


@router.get("/topics")
async def list_topics():
    try:
        snapshot = ros_graph_monitor.get_full_snapshot()
        return {"topics": snapshot.get("topics", [])}
    except Exception as exc:
        logger.error("获取话题列表失败: %s", exc)
        raise HTTPException(status_code=500, detail={"message": str(exc)})
