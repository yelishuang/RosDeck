"""
ROS-AI motion planning service that proxies natural language commands to an external aggregator.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Defaults for the aggregation proxy; overridable via environment variables.
DEFAULT_API_HOST = "api.gpt.ge"
DEFAULT_API_PATH = "/v1/chat/completions"
DEFAULT_MODEL_NAME = "gpt-5-mini-2025-08-07"
DEFAULT_API_KEY = (
    "sk-cbc3l7blij1LjscKF0BcD866Ff3e4d4799852387D17d3c54"
)

ENV_HOST = "ROSDECK_AI_PROXY_HOST"
ENV_PATH = "ROSDECK_AI_PROXY_PATH"
ENV_MODEL = "ROSDECK_AI_PROXY_MODEL"
ENV_KEY = "ROSDECK_AI_PROXY_KEY"

PROXY_ENV_VARS = [
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
    "no_proxy",
    "NO_PROXY",
]


class RosAIAggregatorError(RuntimeError):
    """Raised when the AI aggregation proxy call fails."""


class RosAIAggregatorConfigError(RosAIAggregatorError):
    """Raised when required proxy configuration is missing."""


@dataclass(frozen=True)
class _ProxyConfig:
    host: str
    path: str
    model: str
    api_key: str


class RosAIPlanner:
    """Encapsulates the RosDeck AI proxy interaction workflow."""

    def __init__(self) -> None:
        self._proxy_cleared = False
        self._proxy_lock = threading.Lock()

    # ------------------------ Public interface ------------------------ #
    async def generate_motion_plan(
        self,
        command: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Asynchronously produce a turtlesim motion plan and return {"commands": [...]}.

        Delegates to the synchronous helper via FastAPI's threadpool to avoid blocking.
        """
        from fastapi.concurrency import run_in_threadpool

        return await run_in_threadpool(
            self._generate_motion_plan_sync,
            command,
            history,
        )

    def get_status(self) -> Dict[str, Any]:
        """
        Return the current proxy configuration status for health checks.
        """
        try:
            config = self._load_config()
        except RosAIAggregatorConfigError as exc:
            return {
                "connected": False,
                "ready": False,
                "configured": False,
                "message": str(exc),
            }

        return {
            "connected": True,
            "ready": True,
            "configured": True,
            "model": config.model,
            "host": config.host,
        }

    # ------------------------ Synchronous implementation ------------------------ #
    def _generate_motion_plan_sync(
        self,
        command: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        command = (command or "").strip()
        if not command:
            raise RosAIAggregatorError("指令内容为空")

        self._clear_proxy_environment_once()
        config = self._load_config()

        payload_exact = self._build_payload(
            command,
            history=history,
            tool_choice_mode="exact",
            model=config.model,
        )
        payload_required = self._build_payload(
            command,
            history=history,
            tool_choice_mode="required",
            model=config.model,
        )

        api_response = self._call_api_with_fallback(
            payload_exact,
            payload_required,
            config=config,
        )
        return self._extract_json_content(api_response)

    # ------------------------ Proxy environment handling ------------------------ #
    def _clear_proxy_environment_once(self) -> None:
        if self._proxy_cleared:
            return
        with self._proxy_lock:
            if self._proxy_cleared:
                return
            removed: List[str] = []
            for var in PROXY_ENV_VARS:
                if var in os.environ:
                    removed.append(f"{var}={os.environ.pop(var)}")
            if removed:
                logger.info(
                    "ROS-AI: 已清理以下代理相关环境变量以避免冲突：%s",
                    ", ".join(removed),
                )
            self._proxy_cleared = True

    # ------------------------ Configuration helpers ------------------------ #
    def _load_config(self) -> _ProxyConfig:
        host = os.getenv(ENV_HOST, DEFAULT_API_HOST).strip() or DEFAULT_API_HOST
        path = os.getenv(ENV_PATH, DEFAULT_API_PATH).strip() or DEFAULT_API_PATH
        model = os.getenv(ENV_MODEL, DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME
        api_key = os.getenv(ENV_KEY, DEFAULT_API_KEY).strip()

        if not api_key:
            raise RosAIAggregatorConfigError(
                f"未配置 {ENV_KEY}，无法调用 ROS-AI 中转站。"
            )
        if api_key == DEFAULT_API_KEY:
            logger.warning("ROS-AI: 正在使用内置测试 API Key，请在生产环境中通过 %s 配置独立密钥。", ENV_KEY)

        return _ProxyConfig(host=host, path=path, model=model, api_key=api_key)

    # ------------------------ Prompt builders ------------------------ #
    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "You are a turtlesim motion command compiler.\n"
            "- Respond with EXACTLY ONE tool call to emit_commands.\n"
            "- The tool arguments MUST contain the FULL ordered plan under 'commands' (array of actions).\n"
            "- NEVER output any natural-language content or markdown.\n"
            "- Actions allowed: move, rotate, stop (motion only).\n"
            "- Units: distance in meters; angles in degrees; speeds in m/s and deg/s.\n"
            "- Interpret CW (clockwise) as negative angular.z and CCW as positive in ROS."
        )

    @staticmethod
    def _build_user_prompt(command: str) -> str:
        examples = (
            "Ontology (motion only):\n"
            "  move(linear_speed, distance, is_forward, unit='meter')\n"
            "  rotate(angular_velocity, angle, is_clockwise, unit='degree')\n"
            "  stop()   # publish zero twist once\n\n"
            "You MUST call emit_commands once with the full plan.\n\n"
            "Examples:\n"
            "  prompt: Move forward 1 m at 0.5 m/s\n"
            "  emit_commands({\"commands\":[{\"action\":\"move\",\"params\":{\"linear_speed\":0.5,\"distance\":1,\"is_forward\":true,\"unit\":\"meter\"}}]})\n\n"
            "  prompt: Rotate 90 degrees clockwise at 20 deg/s, then stop\n"
            "  emit_commands({\"commands\":[\n"
            "    {\"action\":\"rotate\",\"params\":{\"angular_velocity\":20,\"angle\":90,\"is_clockwise\":true,\"unit\":\"degree\"}},\n"
            "    {\"action\":\"stop\",\"params\":{}}\n"
            "  ]})\n\n"
            "  prompt: Move back 2 meters at 0.8 m/s and then turn left 45 degrees at 15 deg/s\n"
            "  emit_commands({\"commands\":[\n"
            "    {\"action\":\"move\",\"params\":{\"linear_speed\":0.8,\"distance\":2,\"is_forward\":false,\"unit\":\"meter\"}},\n"
            "    {\"action\":\"rotate\",\"params\":{\"angular_velocity\":15,\"angle\":45,\"is_clockwise\":false,\"unit\":\"degree\"}}\n"
            "  ]})\n\n"
        )
        return f"{examples}prompt: {command}"

    @staticmethod
    def _build_tools() -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "emit_commands",
                    "description": "Return the full ordered motion command list (move/rotate/stop only).",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "commands": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["action"],
                                    "properties": {
                                        "action": {
                                            "type": "string",
                                            "enum": ["move", "rotate", "stop"],
                                        },
                                        "params": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "properties": {
                                                "linear_speed": {
                                                    "type": "number",
                                                    "minimum": 0,
                                                },
                                                "distance": {
                                                    "type": "number",
                                                    "minimum": 0,
                                                },
                                                "is_forward": {"type": "boolean"},
                                                "unit": {
                                                    "type": "string",
                                                    "enum": [
                                                        "meter",
                                                        "meters",
                                                        "m",
                                                        "degree",
                                                        "degrees",
                                                        "deg",
                                                    ],
                                                },
                                                "angular_velocity": {
                                                    "type": "number",
                                                    "minimum": 0,
                                                },
                                                "angle": {"type": "number"},
                                                "is_clockwise": {"type": "boolean"},
                                            },
                                        },
                                    },
                                },
                            }
                        },
                        "required": ["commands"],
                    },
                },
            }
        ]

    def _build_payload(
        self,
        command: str,
        *,
        history: Optional[List[Dict[str, str]]],
        tool_choice_mode: str,
        model: str,
    ) -> Dict[str, Any]:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()}
        ]

        # Include up to four alternating user/assistant messages for lightweight context.
        if history:
            for entry in history[-4:]:
                role = entry.get("role")
                content = entry.get("content")
                if role not in {"user", "assistant"} or not content:
                    continue
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": self._build_user_prompt(command)})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "top_p": 1.0,
            "max_tokens": 512,
            "stream": False,
            "tools": self._build_tools(),
            "parallel_tool_calls": False,
        }

        if tool_choice_mode == "exact":
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": "emit_commands"},
            }
        else:
            payload["tool_choice"] = "required"

        return payload

    # ------------------------ API interaction ------------------------ #
    def _call_api(
        self,
        *,
        payload: Dict[str, Any],
        config: _ProxyConfig,
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        conn = http.client.HTTPSConnection(config.host, timeout=30)
        try:
            conn.request("POST", config.path, body=json.dumps(payload), headers=headers)
            response = conn.getresponse()
            raw = response.read()
        except OSError as exc:
            raise RosAIAggregatorError(f"无法连接到 ROS-AI 中转站：{exc}") from exc
        finally:
            conn.close()

        if response.status != 200:
            body = raw.decode("utf-8", errors="ignore")
            raise RosAIAggregatorError(
                f"中转站返回错误状态 {response.status}: {body}"
            )

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RosAIAggregatorError(
                f"解析中转站 JSON 响应失败: {exc}"
            ) from exc

    def _call_api_with_fallback(
        self,
        payload_exact: Dict[str, Any],
        payload_required: Dict[str, Any],
        *,
        config: _ProxyConfig,
    ) -> Dict[str, Any]:
        try:
            return self._call_api(payload=payload_exact, config=config)
        except RosAIAggregatorError as exc:
            msg = str(exc)
            if "tool_choice" in msg and any(
                term in msg.lower() for term in ("invalid", "unsupported", "not permitted")
            ):
                logger.warning("ROS-AI: tool_choice=exact 不被支持，尝试使用 required 降级。")
                return self._call_api(payload=payload_required, config=config)
            raise

    # ------------------------ Response parsing ------------------------ #
    @staticmethod
    def _extract_json_content(api_response: Dict[str, Any]) -> Dict[str, Any]:
        choices = api_response.get("choices")
        if not choices:
            raise RosAIAggregatorError("中转站响应缺少 choices 字段")

        message = choices[0].get("message", {})
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            raise RosAIAggregatorError("中转站响应缺少 tool_calls 信息")

        for call in tool_calls:
            func = call.get("function") or {}
            if func.get("name") != "emit_commands":
                continue
            arguments = func.get("arguments", "{}")
            try:
                args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
            except json.JSONDecodeError as exc:
                raise RosAIAggregatorError(f"解析 emit_commands 参数失败: {exc}") from exc

            commands = args.get("commands")
            if not isinstance(commands, list) or not commands:
                raise RosAIAggregatorError("emit_commands 返回的 commands 为空或格式错误")

            normalized: List[Dict[str, Any]] = []
            for item in commands:
                if not isinstance(item, dict):
                    continue
                action = item.get("action")
                params = item.get("params") or {}
                if not isinstance(params, dict):
                    try:
                        params = json.loads(params)
                    except Exception:  # noqa: BLE001
                        params = {}
                normalized.append({"action": action, "params": params})

            if not normalized:
                raise RosAIAggregatorError("commands 列表为空或无有效动作")

            return {"commands": normalized}

        raise RosAIAggregatorError("未找到 emit_commands 的 tool 调用")


# Shared singleton instance.
ros_ai_planner = RosAIPlanner()
