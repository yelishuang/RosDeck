"""
ROS communication monitoring endpoints.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.services.ros_comm_monitor import ros_comm_monitor

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ros", tags=["ROS Communication"])

_TIMERANGE_MAP = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
}


class ServiceCallRequest(BaseModel):
    params: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="服务调用的请求参数，按 ROS 服务定义填写",
    )


def _parse_timerange(timerange: str) -> int:
    if timerange in _TIMERANGE_MAP:
        return _TIMERANGE_MAP[timerange]

    pattern = re.fullmatch(r"(?P<value>\d+)(?P<unit>[smh])", timerange.strip())
    if not pattern:
        raise ValueError("timerange 参数格式不正确")

    value = int(pattern.group("value"))
    unit = pattern.group("unit")

    if unit == "s":
        return max(1, min(value, ros_comm_monitor.ANALYSIS_WINDOW_SECONDS))
    if unit == "m":
        seconds = value * 60
    else:  # unit == "h"
        seconds = value * 3600

    return max(1, min(seconds, ros_comm_monitor.ANALYSIS_WINDOW_SECONDS))


def _handle_runtime_error(exc: RuntimeError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"message": str(exc)},
    )


@router.get("/topics")
async def list_topics():
    """
    List ROS topics along with basic transport statistics.
    """
    try:
        topics = ros_comm_monitor.list_topics()
        return {"success": True, "topics": topics}
    except RuntimeError as exc:
        raise _handle_runtime_error(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive fallback.
        logger.exception("获取话题列表失败: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "获取话题列表失败", "error": str(exc)},
        ) from exc


@router.get("/topics/{topic_name:path}/messages")
async def get_topic_messages(topic_name: str, limit: int = 10):
    """
    Return the most recent messages published on a topic.
    """
    limit = max(1, min(limit, ros_comm_monitor.MESSAGE_CACHE_LIMIT))
    try:
        messages = ros_comm_monitor.get_messages(topic_name, limit)
        return {"success": True, "messages": messages}
    except RuntimeError as exc:
        raise _handle_runtime_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": str(exc)},
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive fallback.
        logger.exception("获取话题 %s 消息失败: %s", topic_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "获取话题消息失败", "error": str(exc)},
        ) from exc


@router.post("/topics/{topic_name:path}/analysis")
async def analyse_topic(topic_name: str, timerange: str = "5m"):
    """
    Produce message statistics for a topic over a requested time window.
    """
    try:
        window_seconds = _parse_timerange(timerange)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": str(exc)},
        ) from exc

    try:
        analysis = ros_comm_monitor.analyse_topic(topic_name, window_seconds)
        return {"success": True, "analysis": analysis}
    except RuntimeError as exc:
        raise _handle_runtime_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": str(exc)},
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive fallback.
        logger.exception("分析话题 %s 失败: %s", topic_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "话题分析失败", "error": str(exc)},
        ) from exc


@router.get("/services")
async def list_services():
    """
    Enumerate available ROS services along with metadata.
    """
    if not ros_comm_monitor.is_available():
        return {
            "success": True,
            "services": [],
            "message": "ROS 通信监控不可用，返回空列表",
        }

    try:
        services = ros_comm_monitor.list_services()
        return {"success": True, "services": services}
    except RuntimeError as exc:
        raise _handle_runtime_error(exc) from exc
    except Exception as exc:  # pragma: no cover - defensive fallback.
        logger.exception("获取服务列表失败: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "获取服务列表失败", "error": str(exc)},
        ) from exc


@router.post("/services/{service_name:path}/call")
async def call_service(
    service_name: str,
    payload: ServiceCallRequest = Body(default_factory=ServiceCallRequest),
):
    """
    Invoke a ROS service and return the response payload details.
    """
    start = time.perf_counter()
    try:
        result = await run_in_threadpool(
            ros_comm_monitor.call_service,
            service_name,
            payload.params or {},
        )
        duration_ms = (time.perf_counter() - start) * 1000.0
        return {
            "success": True,
            "result": result.get("result"),
            "type": result.get("type"),
            "duration_ms": duration_ms,
        }
    except RuntimeError as exc:
        raise _handle_runtime_error(exc) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": str(exc)},
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive fallback.
        logger.exception("调用服务 %s 失败: %s", service_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "服务调用失败", "error": str(exc)},
        ) from exc


@router.get("/message-types")
async def list_message_types():
    """
    获取当前使用到的消息类型
    """
    try:
        types = ros_comm_monitor.list_message_types()
        return {"success": True, "types": types}
    except RuntimeError as exc:
        raise _handle_runtime_error(exc) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("获取消息类型失败: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "获取消息类型失败", "error": str(exc)},
        ) from exc


@router.get("/message-types/{type_name:path}")
async def get_message_type(type_name: str):
    """
    获取消息类型的定义与字段信息
    """
    try:
        details = ros_comm_monitor.get_message_type_details(type_name)
        return {"success": True, "type": details}
    except RuntimeError as exc:
        raise _handle_runtime_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"message": str(exc)},
        ) from exc
    except Exception as exc:  # pragma: no cover
        logger.exception("获取消息类型 %s 详情失败: %s", type_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "获取消息类型详情失败", "error": str(exc)},
        ) from exc
