"""
ROS-AI 指挥官路由
处理 turtlesim 的启动、控制和 AI 指令
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.services.ros_ai_planner import (
    RosAIAggregatorConfigError,
    RosAIAggregatorError,
    ros_ai_planner,
)
from app.services.turtlesim_capture import TurtlesimCaptureError, turtlesim_capture
from app.services.turtlesim_manager import turtlesim_manager
from app.ws.turtlesim_ws import turtlesim_websocket_endpoint

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ros", tags=["ROS-AI"])


def _format_number(value: Any, suffix: str) -> Optional[str]:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if abs(num) >= 100:
        formatted = f"{num:.1f}"
    else:
        formatted = f"{num:.2f}"
    formatted = formatted.rstrip("0").rstrip(".")
    return f"{formatted}{suffix}"


def _summarize_motion_plan(commands: List[Dict[str, Any]]) -> str:
    if not commands:
        return "动作计划为空，未发送运动指令。"

    summaries: List[str] = []
    for idx, command in enumerate(commands, start=1):
        action = (command.get("action") or "").lower()
        params = command.get("params") or {}

        if action == "move":
            direction = "前进" if params.get("is_forward", True) else "后退"
            distance = _format_number(params.get("distance"), "m")
            speed = _format_number(params.get("linear_speed"), "m/s")
            parts = [direction]
            if distance:
                parts.append(distance)
            if speed:
                parts.append(f"速度{speed}")
            summary = "，".join(parts)
        elif action == "rotate":
            direction = "顺时针" if params.get("is_clockwise", True) else "逆时针"
            angle = _format_number(params.get("angle"), "°")
            speed = _format_number(params.get("angular_velocity"), "°/s")
            parts = [f"{direction}旋转"]
            if angle:
                parts.append(angle)
            if speed:
                parts.append(f"角速度{speed}")
            summary = "，".join(parts)
        elif action == "stop":
            summary = "停止运动"
        else:
            summary = f"执行 {action or '未知动作'}"

        summaries.append(f"{idx}. {summary}")

    return f"动作计划生成成功，共 {len(commands)} 步：{'；'.join(summaries)}"


class AICommandRequest(BaseModel):
    """AI 指令请求"""
    message: str
    history: Optional[List[Dict[str, str]]] = None


class VelocityCommandRequest(BaseModel):
    """速度控制请求"""
    linear: float
    angular: float
    duration: Optional[float] = None  # 持续时间（秒）


@router.post("/ai/command")
async def process_ai_command(request: AICommandRequest):
    """接收前端指令，调用 AI 中转站生成动作计划并驱动 turtlesim。"""
    try:
        plan = await ros_ai_planner.generate_motion_plan(
            request.message,
            history=request.history,
        )
    except RosAIAggregatorConfigError as exc:
        logger.warning("AI 中转站配置缺失: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"message": "AI 服务未配置", "error": str(exc)},
        ) from exc
    except RosAIAggregatorError as exc:
        logger.error("调用 AI 中转站失败: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={"message": "调用 AI 中转站失败", "error": str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception("生成动作计划时出现未预期异常: %s", exc)
        raise HTTPException(
            status_code=500,
            detail={"message": "生成动作计划失败", "error": str(exc)},
        ) from exc

    commands = plan.get("commands") or []

    try:
        turtlesim_manager.execute_motion_plan(commands)
    except RuntimeError as exc:
        logger.warning("执行动作计划失败（turtlesim 未就绪？）: %s", exc)
        raise HTTPException(
            status_code=409,
            detail={"message": "Turtlesim 未就绪", "error": str(exc)},
        ) from exc
    except ValueError as exc:
        logger.warning("AI 返回了空动作计划: %s", exc)
        raise HTTPException(
            status_code=400,
            detail={"message": "动作计划为空", "error": str(exc)},
        ) from exc
    except Exception as exc:
        logger.exception("执行动作计划时出现异常: %s", exc)
        raise HTTPException(
            status_code=500,
            detail={"message": "执行动作计划失败", "error": str(exc)},
        ) from exc

    reply = _summarize_motion_plan(commands)
    logger.info("AI 动作计划已下发：%s", commands)

    return {
        "success": True,
        "reply": reply,
        "command": plan,
        "message": request.message,
    }


@router.get("/ai/status")
async def get_ai_status():
    """获取 AI 服务状态"""
    return ros_ai_planner.get_status()


@router.post("/turtlesim/start")
async def start_turtlesim():
    """启动 turtlesim"""
    try:
        logger.info("收到前端启动 turtlesim 请求")
        result = turtlesim_manager.start_turtlesim()
        if result['success']:
            logger.info("turtlesim 启动成功: %s", result)
            return result
        else:
            logger.warning("turtlesim 启动失败: %s", result)
            raise HTTPException(status_code=500, detail=result)
    except Exception as e:
        logger.error(f"启动 turtlesim 失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "启动失败", "error": str(e)}
        )


@router.post("/turtlesim/stop")
async def stop_turtlesim():
    """停止 turtlesim"""
    try:
        result = turtlesim_manager.stop_turtlesim()
        if result['success']:
            return result
        else:
            raise HTTPException(status_code=500, detail=result)
    except Exception as e:
        logger.error(f"停止 turtlesim 失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "停止失败", "error": str(e)}
        )


@router.get("/turtlesim/status")
async def get_turtlesim_status():
    """获取 turtlesim 状态"""
    try:
        status = turtlesim_manager.get_status()
        return {
            "success": True,
            "status": status
        }
    except Exception as e:
        logger.error(f"获取状态失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "获取状态失败", "error": str(e)}
        )


@router.get("/turtlesim/stream")
async def stream_turtlesim():
    """以 MJPEG 形式输出 turtlesim 窗口视频流"""
    if not turtlesim_manager.is_running:
        raise HTTPException(
            status_code=409,
            detail={"message": "turtlesim 未运行，无法提供视频流", "code": "TURTLESIM_NOT_RUNNING"},
        )

    try:
        logger.debug("准备定位 turtlesim 窗口用于视频流")
        window_id = await turtlesim_capture.ensure_window_id()
    except TurtlesimCaptureError as exc:
        logger.warning("定位 turtlesim 窗口失败: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"message": str(exc), "code": exc.code},
        ) from exc

    logger.debug("开始推送 turtlesim 视频流，窗口 ID=%s", window_id)
    stream = turtlesim_capture.mjpeg_stream(window_id)
    headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
    }
    return StreamingResponse(
        stream,
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers=headers,
    )


@router.post("/turtlesim/velocity")
async def send_velocity(request: VelocityCommandRequest):
    """发送速度控制命令"""
    try:
        success = turtlesim_manager.send_velocity_command(
            request.linear,
            request.angular
        )

        if success:
            # 如果指定了持续时间，启动定时停止任务
            if request.duration:
                async def stop_after_duration():
                    await asyncio.sleep(request.duration)
                    turtlesim_manager.send_velocity_command(0.0, 0.0)

                asyncio.create_task(stop_after_duration())

            return {
                "success": True,
                "message": "速度命令已发送",
                "linear": request.linear,
                "angular": request.angular,
                "duration": request.duration
            }
        else:
            raise HTTPException(
                status_code=500,
                detail={"message": "发送速度命令失败"}
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"发送速度命令失败: {e}")
        raise HTTPException(
            status_code=500,
            detail={"message": "发送速度命令失败", "error": str(e)}
        )


# WebSocket 端点
@router.websocket("/ws/turtle")
async def turtle_websocket(websocket: WebSocket):
    """
    Turtlesim WebSocket 连接
    """
    await turtlesim_websocket_endpoint(websocket)
