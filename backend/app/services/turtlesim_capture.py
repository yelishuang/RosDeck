"""
Turtlesim 窗口捕获服务
负责定位 turtlesim 窗口并提供 MJPEG 视频流。
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)


class TurtlesimCaptureError(RuntimeError):
    """窗口捕获相关异常。"""

    def __init__(self, message: str, code: str = "CAPTURE_ERROR"):
        super().__init__(message)
        self.code = code


@dataclass
class _WindowCandidate:
    window_id: str
    parent_id: Optional[str]
    title: str
    area: int


class TurtlesimCapture:
    """
    封装窗口查找与 MJPEG 视频流生成。
    """

    def __init__(self, keyword: str = "TurtleSim") -> None:
        self.keyword = keyword
        self._cache_lock = asyncio.Lock()
        self._cached_window_id: Optional[str] = None

    def reset_cache(self) -> None:
        """清理缓存的窗口 ID。"""
        self._cached_window_id = None

    async def ensure_window_id(self, retries: int = 5, delay: float = 0.6) -> str:
        """
        获取窗口 ID，必要时重试。
        """
        async with self._cache_lock:
            logger.debug(
                "ensure_window_id invoked (cached=%s)", self._cached_window_id or "None"
            )
            if self._cached_window_id and self._window_exists(self._cached_window_id):
                logger.debug("使用缓存的 turtlesim 窗口 ID: %s", self._cached_window_id)
                return self._cached_window_id

            last_error: Optional[Exception] = None
            for attempt in range(1, retries + 1):
                logger.debug("第 %s/%s 次尝试定位 turtlesim 窗口", attempt, retries)
                try:
                    window_id = await asyncio.get_event_loop().run_in_executor(
                        None, self._find_window_id_sync
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.debug("查找 turtlesim 窗口失败（第 %s 次）: %s", attempt, exc)
                    await asyncio.sleep(delay)
                    continue

                if window_id:
                    logger.info("已定位 turtlesim 窗口: %s", window_id)
                    self._cached_window_id = window_id
                    return window_id

                await asyncio.sleep(delay)

            message = (
                f"未能在 {retries} 次尝试后定位到 turtlesim 窗口。"
                "请确认 turtlesim 已启动且运行在当前 X11 会话中。"
            )
            if last_error:
                logger.error("%s 详细错误: %s", message, last_error)
            raise TurtlesimCaptureError(message, code="WINDOW_NOT_FOUND")

    def _find_window_id_sync(self) -> Optional[str]:
        command = ["xwininfo", "-root", "-tree"]
        try:
            logger.debug("执行命令: %s", " ".join(command))
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            logger.debug("xwininfo 命令执行完成，输出长度: %s", len(result.stdout))
        except FileNotFoundError as exc:  # noqa: BLE001
            raise TurtlesimCaptureError("未找到 xwininfo 命令", code="XWININFO_MISSING") from exc
        except subprocess.CalledProcessError as exc:  # noqa: BLE001
            raise TurtlesimCaptureError(
                f"xwininfo 执行失败: {exc.stderr or exc.stdout}", code="XWININFO_FAILED"
            ) from exc

        lines = result.stdout.splitlines()
        candidates = self._collect_candidates(lines)
        if not candidates:
            logger.warning("未在 xwininfo 输出中找到包含 '%s' 的窗口", self.keyword)
            return None

        # 选择面积最大的候选，通常为内容窗口
        best = max(candidates, key=lambda item: item.area)
        logger.debug(
            "匹配到窗口: title=%s, window=%s, parent=%s, area=%s",
            best.title,
            best.window_id,
            best.parent_id,
            best.area,
        )
        return best.window_id

    def _collect_candidates(self, lines: list[str]) -> list[_WindowCandidate]:
        pattern = re.compile(
            r'^\s*(0x[a-f0-9]+)\s+"([^"]*%s[^"]*)".*?([0-9]+)x([0-9]+)'
            % re.escape(self.keyword),
            re.IGNORECASE,
        )
        candidates: list[_WindowCandidate] = []

        for idx, line in enumerate(lines):
            match = pattern.search(line)
            if not match:
                continue

            window_id, title, width, height = match.groups()
            width_i, height_i = int(width), int(height)
            if width_i < 50 or height_i < 50:
                logger.debug(
                    "忽略窗口 %s (title=%s) 尺寸过小: %sx%s", window_id, title, width, height
                )
                continue

            parent_id = self._find_parent_id(lines, idx)
            area = width_i * height_i
            candidates.append(
                _WindowCandidate(window_id=window_id, parent_id=parent_id, title=title, area=area)
            )
            logger.info(
                "匹配到候选窗口: id=%s parent=%s title='%s' size=%sx%s area=%s",
                window_id,
                parent_id,
                title,
                width,
                height,
                area,
            )
        return candidates

    @staticmethod
    def _find_parent_id(lines: list[str], child_index: int) -> Optional[str]:
        child_indent = len(lines[child_index]) - len(lines[child_index].lstrip(" "))
        for i in range(child_index - 1, -1, -1):
            line = lines[i]
            if not line.strip():
                continue
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent < child_indent:
                match = re.match(r"^\s*(0x[a-f0-9]+)", line)
                if match:
                    return match.group(1)
        return None

    def _window_exists(self, window_id: str) -> bool:
        command = ["xwininfo", "-id", window_id]
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.debug("窗口 %s 仍然存在", window_id)
            return True
        except subprocess.CalledProcessError:
            logger.debug("窗口 %s 不存在，将重新定位", window_id)
            return False
        except FileNotFoundError:
            # 如果 xwininfo 本身不存在，上一阶段也会失败，这里保持 False
            logger.debug("检测窗口时未找到 xwininfo 命令")
            return False

    async def mjpeg_stream(
        self,
        window_id: str,
        framerate: int = 15,
        quality: int = 5,
        scale_width: int = 640,
    ) -> AsyncGenerator[bytes, None]:
        """
        生成 MJPEG 视频流。
        """
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "x11grab",
            "-draw_mouse",
            "0",
            "-framerate",
            str(framerate),
            "-window_id",
            window_id,
            "-i",
            ":0.0",
            "-vf",
            f"scale={scale_width}:-1",
            "-f",
            "mjpeg",
            "-q:v",
            str(quality),
            "pipe:1",
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info(
                "已启动 FFmpeg 捕获 turtlesim 视频流 (window_id=%s, framerate=%s, scale=%s)",
                window_id,
                framerate,
                scale_width,
            )
        except FileNotFoundError as exc:  # noqa: BLE001
            raise TurtlesimCaptureError("未找到 ffmpeg 命令", code="FFMPEG_MISSING") from exc

        boundary = b"--frame\r\n"
        buffer = b""

        async def _kill_process() -> None:
            if process.returncode is None:
                process.kill()
                await process.communicate()
                logger.debug("FFmpeg 进程已结束 (window_id=%s)", window_id)

        try:
            while True:
                chunk = await process.stdout.read(4096) if process.stdout else b""
                if not chunk:
                    break
                buffer += chunk

                while True:
                    start = buffer.find(b"\xff\xd8")  # JPEG 开头
                    end = buffer.find(b"\xff\xd9")  # JPEG 结尾
                    if start != -1 and end != -1 and end > start:
                        frame = buffer[start : end + 2]
                        buffer = buffer[end + 2 :]
                        header = (
                            boundary
                            + b"Content-Type: image/jpeg\r\n"
                            + b"Content-Length: "
                            + str(len(frame)).encode("ascii")
                            + b"\r\n\r\n"
                        )
                        yield header + frame + b"\r\n"
                    else:
                        # 保持 buffer 不无限增长
                        if start == -1 and len(buffer) > 1_000_000:
                            buffer = buffer[-100_000:]
                        break
        except asyncio.CancelledError:  # pragma: no cover - 用户取消时执行
            logger.debug("视频流任务被取消 (window_id=%s)", window_id)
            await _kill_process()
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("推送 MJPEG 流时发生异常: %s", exc)
            await _kill_process()
            raise
        finally:
            await _kill_process()


# 单例实例供路由使用
turtlesim_capture = TurtlesimCapture()
