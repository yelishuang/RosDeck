#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ROS2 turtlesim（小乌龟）专用：自然语言 → 结构化命令 → 立即驱动 /turtle1/cmd_vel

特点
- 继续采用“聚合器工具” emit_commands：模型一次性返回 {"commands":[...]}；
- 只保留运动相关动作：move / rotate / stop（忽略传送/生成/清理等服务指令）；
- 在打印 JSON 的同时，顺序发布 Twist 到 /turtle1/cmd_vel 让小乌龟动起来；
- 角速度单位：输入为度/秒，执行层自动转换为弧度/秒；顺时针为负、逆时针为正（ROS右手系）。

依赖
- 已安装 ROS 2（rclpy、geometry_msgs）并运行 turtlesim_node：
    ros2 run turtlesim turtlesim_node
"""

import http.client
import json
import os
import sys
import time
import math
from typing import Any, Dict, List, Optional

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

# ==========================
# 中转站配置（根据你的环境可保持不变）
# ==========================
API_HOST = "api.gpt.ge"
API_PATH = "/v1/chat/completions"
API_KEY  = "sk-cbc3l7blij1LjscKF0BcD866Ff3e4d4799852387D17d3c54"
MODEL_NAME = "gpt-5-mini-2025-08-07"

# 需要清理的代理变量，避免与中转站冲突
PROXY_ENV_VARS = [
    "http_proxy", "https_proxy",
    "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
    "no_proxy",  "NO_PROXY",
]

def clear_proxy_environment() -> None:
    removed = []
    for var in PROXY_ENV_VARS:
        if var in os.environ:
            removed.append(f"{var}={os.environ.pop(var)}")
    if removed:
        print("[INFO] 已清理以下代理变量：")
        for item in removed:
            print(f"        {item}")

# ==========================
# 提示词 & 工具（仅运动动作）
# ==========================

def build_system_prompt() -> str:
    return (
        "You are a turtlesim motion command compiler.\n"
        "- Respond with EXACTLY ONE tool call to emit_commands.\n"
        "- The tool arguments MUST contain the FULL ordered plan under 'commands' (array of actions).\n"
        "- NEVER output any natural-language content or markdown.\n"
        "- Actions allowed: move, rotate, stop (motion only).\n"
        "- Units: distance in meters; angles in degrees; speeds in m/s and deg/s.\n"
        "- Interpret CW (clockwise) as negative angular.z and CCW as positive in ROS."
    )

def build_user_prompt(command: str) -> str:
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
    return examples + "prompt: " + command

def build_tools() -> List[Dict[str, Any]]:
    """聚合器函数，仅允许 move/rotate/stop 三类动作。"""
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
                                    "action": { "type": "string", "enum": ["move", "rotate", "stop"] },
                                    "params": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "properties": {
                                            # move
                                            "linear_speed": {"type": "number", "minimum": 0},
                                            "distance": {"type": "number", "minimum": 0},
                                            "is_forward": {"type": "boolean"},
                                            "unit": {"type": "string", "enum": ["meter","meters","m","degree","degrees","deg"]},
                                            # rotate
                                            "angular_velocity": {"type": "number", "minimum": 0},
                                            "angle": {"type": "number"},
                                            "is_clockwise": {"type": "boolean"}
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "required": ["commands"]
                }
            }
        }
    ]

# ==========================
# 请求 / 解析
# ==========================

def build_payload(command: str, tool_choice_mode: str = "exact") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_prompt(command)},
        ],
        "temperature": 0.1,
        "top_p": 1.0,
        "max_tokens": 512,
        "stream": False,
        "tools": build_tools(),
        "parallel_tool_calls": False
    }
    if tool_choice_mode == "exact":
        payload["tool_choice"] = {"type": "function", "function": {"name": "emit_commands"}}
    else:
        payload["tool_choice"] = "required"
    return payload

def _http_post_json(host: str, path: str, body: Dict[str, Any], headers: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    conn.request("POST", path, body=json.dumps(body), headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status}: {raw.decode('utf-8', errors='ignore')}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法解析 API 返回的 JSON: {exc}\n原始内容: {raw!r}") from exc

def call_api_with_fallback(payload_exact: Dict[str, Any], payload_required: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    try:
        return _http_post_json(API_HOST, API_PATH, payload_exact, headers)
    except RuntimeError as e:
        msg = str(e)
        if "tool_choice" in msg and ("invalid" in msg.lower() or "unsupported" in msg.lower() or "not permitted" in msg.lower()):
            print("[WARN] tool_choice 对象写法可能不被网关支持，尝试降级为 'required' ...")
            return _http_post_json(API_HOST, API_PATH, payload_required, headers)
        raise

def extract_json_content(api_response: Dict[str, Any]) -> Dict[str, Any]:
    """提取 emit_commands 的 arguments 并返回 {"commands":[...]}。"""
    choices = api_response.get("choices")
    if not choices:
        raise RuntimeError(f"API 响应缺少 choices 字段: {api_response}")
    message = choices[0].get("message", {})
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        for call in tool_calls:
            f = call.get("function", {})
            if f.get("name") == "emit_commands":
                arg_str = f.get("arguments", "{}")
                args = json.loads(arg_str) if isinstance(arg_str, str) else (arg_str or {})
                cmds = args.get("commands")
                if isinstance(cmds, list) and cmds:
                    normalized = []
                    for item in cmds:
                        if not isinstance(item, dict) or "action" not in item:
                            continue
                        action = item["action"]
                        params = item.get("params", {})
                        if params is None:
                            params = {}
                        if not isinstance(params, dict):
                            try:
                                params = json.loads(params)
                                if not isinstance(params, dict):
                                    params = {}
                            except Exception:
                                params = {}
                        normalized.append({"action": action, "params": params})
                    if not normalized:
                        raise RuntimeError(f"emit_commands 返回了空或无效的 commands：{cmds}")
                    return {"commands": normalized}
                raise RuntimeError(f"emit_commands 未返回 commands 数组或为空：{args}")
    raise RuntimeError(f"未找到 emit_commands 的 tool_calls：{message}")

# ==========================
# 执行层：发布 /turtle1/cmd_vel
# ==========================

class TurtleCommander(Node):
    def __init__(self):
        super().__init__("turtlesim_motion_commander")
        self.pub = self.create_publisher(Twist, "/turtle1/cmd_vel", 10)
        self.default_rate_hz = 30.0

    def _publish_twist(self, lin_x: float, ang_z: float) -> None:
        msg = Twist()
        msg.linear.x  = float(lin_x)
        msg.linear.y  = 0.0
        msg.linear.z  = 0.0
        msg.angular.x = 0.0
        msg.angular.y = 0.0
        msg.angular.z = float(ang_z)
        self.pub.publish(msg)

    def _sleep(self, seconds: float) -> None:
        # 阻塞式 sleep，期间偶尔 spin 一下（可选）
        end = time.monotonic() + max(0.0, seconds)
        period = 1.0 / self.default_rate_hz
        while time.monotonic() < end:
            time.sleep(min(period, end - time.monotonic()))

    def stop(self) -> None:
        self._publish_twist(0.0, 0.0)
        # 给一点点时间让零速真正送达
        self._sleep(0.05)

    def do_move(self, linear_speed: float, distance: float, is_forward: bool) -> None:
        if linear_speed <= 0 or distance <= 0:
            self.get_logger().warn(f"[MOVE] 忽略无效参数 linear_speed={linear_speed}, distance={distance}")
            return
        duration = distance / linear_speed
        lin = linear_speed if is_forward else -linear_speed
        ang = 0.0
        self.get_logger().info(f"[MOVE] v={lin:.3f} m/s, s={distance:.3f} m, t={duration:.3f} s")
        end = time.monotonic() + duration
        period = 1.0 / self.default_rate_hz
        while time.monotonic() < end:
            self._publish_twist(lin, ang)
            time.sleep(period)
        self.stop()

    def do_rotate(self, angular_velocity_deg_s: float, angle_deg: float, is_clockwise: bool) -> None:
        if angular_velocity_deg_s <= 0 or abs(angle_deg) <= 0:
            self.get_logger().warn(f"[ROTATE] 忽略无效参数 ang_vel={angular_velocity_deg_s}, angle={angle_deg}")
            return
        # 统一把角度正化为正数、方向用 is_clockwise 表示
        angle = abs(angle_deg)
        duration = angle / angular_velocity_deg_s
        omega_rad = math.radians(angular_velocity_deg_s)
        # ROS2: CCW 为正；CW 为负
        ang = -omega_rad if is_clockwise else +omega_rad
        self.get_logger().info(f"[ROTATE] ω={ang:.3f} rad/s ({angular_velocity_deg_s:.3f} deg/s), "
                               f"θ={angle:.3f} deg, t={duration:.3f} s, cw={is_clockwise}")
        end = time.monotonic() + duration
        period = 1.0 / self.default_rate_hz
        while time.monotonic() < end:
            self._publish_twist(0.0, ang)
            time.sleep(period)
        self.stop()

    def execute_commands(self, doc: Dict[str, Any]) -> None:
        cmds = doc.get("commands", [])
        for i, cmd in enumerate(cmds, 1):
            action = cmd.get("action")
            params = cmd.get("params", {}) or {}
            self.get_logger().info(f"[EXEC] Step {i}/{len(cmds)}: {action} {params}")
            try:
                if action == "move":
                    v = float(params.get("linear_speed", 0.0))
                    s = float(params.get("distance", 0.0))
                    fwd = bool(params.get("is_forward", True))
                    self.do_move(v, s, fwd)

                elif action == "rotate":
                    av = float(params.get("angular_velocity", 0.0))      # deg/s
                    ang = float(params.get("angle", 0.0))                # deg
                    cw = bool(params.get("is_clockwise", True))
                    self.do_rotate(av, ang, cw)

                elif action == "stop":
                    self.stop()

                else:
                    self.get_logger().warn(f"[EXEC] 未知动作 '{action}'，跳过")
            except KeyboardInterrupt:
                self.get_logger().warn("[EXEC] 用户中断，立即停止")
                self.stop()
                raise
            except Exception as e:
                self.get_logger().error(f"[EXEC] 执行动作 '{action}' 失败：{e}")
                self.stop()

# ==========================
# CLI 主循环
# ==========================

def main() -> None:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本。", file=sys.stderr)
        sys.exit(1)

    clear_proxy_environment()

    rclpy.init(args=None)
    node = TurtleCommander()
    print("=== ROSGPT · turtlesim · 命令行转 JSON + 立即执行（/turtle1/cmd_vel） ===")
    print("确保已运行：ros2 run turtlesim turtlesim_node")
    print("输入自然语言指令，回车发送；输入空行或 Ctrl+C 退出。")

    try:
        while True:
            try:
                user_input = input("\n指令> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n退出。")
                break

            if not user_input:
                print("收到空指令，退出。")
                break

            # 构造两份 payload：精确 tool_choice 与 required 降级
            payload_exact = build_payload(user_input, tool_choice_mode="exact")
            payload_required = build_payload(user_input, tool_choice_mode="required")

            try:
                api_response = call_api_with_fallback(payload_exact, payload_required)
                structured = extract_json_content(api_response)
            except Exception as exc:
                print(f"[ERROR] {exc}")
                continue

            # 打印 JSON
            print("模型返回 JSON：")
            print(json.dumps(structured, ensure_ascii=False, indent=2))

            # 执行命令（驱动小乌龟）
            node.execute_commands(structured)

    finally:
        # 退出前确保停住
        try:
            node.stop()
        except Exception:
            pass
        node.destroy_node()
        rclpy.shutdown()

if __name__ == "__main__":
    main()
