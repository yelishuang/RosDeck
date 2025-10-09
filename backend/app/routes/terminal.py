"""
终端 WebSocket 路由
提供实时终端访问
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Request
import asyncio
import logging
import uuid
import json

from app.services.terminal_manager import terminal_manager
from app.deps.admin_auth import (
    get_current_username,
    admin_auth,
    extract_username_from_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/terminal", tags=["terminal"])


@router.websocket("/ws")
async def terminal_websocket(websocket: WebSocket):
    """
    WebSocket 终端连接
    根据 Cookie 验证用户身份:
    - session_id: 普通登录会话,解析出用户名
    - admin_session_id: 若存在且有效则视为管理员
    """
    cookie_session_id = websocket.cookies.get("session_id")
    if not cookie_session_id:
        logger.warning("WebSocket handshake rejected: missing session_id cookie")
        await websocket.close(code=4401)
        return

    try:
        username = extract_username_from_session(cookie_session_id)
    except ValueError as exc:
        logger.warning(f"WebSocket handshake rejected: invalid session_id ({exc})")
        await websocket.close(code=4401)
        return

    # 检查管理员状态
    admin_session_id = websocket.cookies.get("admin_session_id")
    admin_auth.cleanup_expired_sessions()
    is_admin = bool(
        admin_session_id and admin_auth.validate_session(admin_session_id)
    )
    admin_remaining = (
        admin_auth.get_remaining_time(admin_session_id)
        if is_admin and admin_session_id
        else 0
    )

    await websocket.accept()

    # 生成会话 ID
    ws_session_id = str(uuid.uuid4())

    # 创建终端会话
    session = terminal_manager.create_session(ws_session_id, username, is_admin)

    # 启动 PTY
    pty_started = await session.start_pty()
    if not pty_started:
        await websocket.send_json({
            'type': 'error',
            'message': '无法启动终端会话'
        })
        await websocket.close(code=1011)
        return

    # 下发会话信息,供前端展示
    await websocket.send_json({
        'type': 'session_info',
        'username': username,
        'is_admin': is_admin,
        'admin_session_expires_in': admin_remaining
    })

    # 发送欢迎消息
    welcome_msg = f"\r\n欢迎使用 RosDeck 终端\r\n"
    if is_admin:
        welcome_msg += f"管理员模式已激活\r\n"
    else:
        welcome_msg += f"普通用户模式 (切换到管理员模式以执行更多命令)\r\n"
    welcome_msg += f"会话将在 30 分钟无活动后自动断开\r\n\r\n"

    await websocket.send_json({
        'type': 'output',
        'data': welcome_msg
    })

    # 启动清理任务
    await terminal_manager.start_cleanup_task()

    # 创建读取任务
    async def read_from_pty():
        """从 PTY 读取输出并发送到客户端"""
        while True:
            try:
                if session.pty and session.pty.isalive():
                    # 读取 PTY 输出
                    data = await session.read()
                    if data:
                        logger.debug("WS %s -> client output: %r", ws_session_id, data)
                        await websocket.send_json({
                            'type': 'output',
                            'data': data
                        })
                    else:
                        await asyncio.sleep(0.01)
                else:
                    # PTY 进程已终止
                    await websocket.send_json({
                        'type': 'error',
                        'message': '终端会话已结束'
                    })
                    break
            except Exception as e:
                logger.error(f"Error reading from PTY: {e}")
                break

    # 启动读取任务
    read_task = asyncio.create_task(read_from_pty())

    try:
        # 处理客户端输入
        while True:
            try:
                # 接收客户端消息
                message = await websocket.receive_text()
                data = json.loads(message)
                logger.debug("WS %s <- client message: %s", ws_session_id, message)

                msg_type = data.get('type')

                if msg_type == 'input':
                    # 用户输入
                    input_data = data.get('data', '')
                    await session.write(input_data)

                elif msg_type == 'resize':
                    # 调整终端大小
                    rows = data.get('rows', 24)
                    cols = data.get('cols', 80)
                    session.resize(rows, cols)
                    logger.debug("WS %s resize -> rows=%s cols=%s", ws_session_id, rows, cols)

                elif msg_type == 'command_check':
                    # 检查命令是否允许执行
                    command = data.get('command', '')
                    allowed, reason = terminal_manager.check_command_allowed(
                        command, session.is_admin
                    )

                    if not allowed:
                        logger.debug("WS %s command blocked: %s (%s)", ws_session_id, command, reason)
                        await websocket.send_json({
                            'type': 'command_blocked',
                            'message': reason,
                            'command': command
                        })
                    else:
                        # 记录命令
                        session.add_command(command)
                        logger.debug("WS %s command allowed: %s", ws_session_id, command)
                        await websocket.send_json({
                            'type': 'command_allowed',
                            'command': command
                        })

                elif msg_type == 'ping':
                    # 心跳
                    logger.debug("WS %s ping", ws_session_id)
                    await websocket.send_json({'type': 'pong'})

            except WebSocketDisconnect:
                logger.info(f"WebSocket disconnected for session {ws_session_id}")
                break
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from client: {message}")
            except Exception as e:
                logger.error(f"Error handling WebSocket message: {e}")
                break

    finally:
        # 清理
        read_task.cancel()
        try:
            await read_task
        except asyncio.CancelledError:
            pass

        terminal_manager.close_session(ws_session_id)
        logger.info(f"Terminal session {ws_session_id} cleaned up")


@router.get("/history")
async def get_command_history(username: str = Depends(get_current_username)):
    """
    获取命令历史记录
    """
    # 查找该用户的活跃会话
    for session in terminal_manager.sessions.values():
        if session.username == username:
            return {
                'success': True,
                'history': session.command_history
            }

    return {
        'success': False,
        'message': '未找到活跃的终端会话',
        'history': []
    }


@router.get("/session-info")
async def get_session_info(
    request: Request,
    username: str = Depends(get_current_username)
):
    """
    返回当前终端相关的会话信息 (用户名 / 管理员状态 / 管理员剩余时间)
    """
    admin_session_id = request.cookies.get("admin_session_id")
    admin_auth.cleanup_expired_sessions()

    is_admin = bool(
        admin_session_id and admin_auth.validate_session(admin_session_id)
    )

    remaining = 0
    if is_admin and admin_session_id:
        remaining = admin_auth.get_remaining_time(admin_session_id)

    return {
        'success': True,
        'username': username,
        'is_admin': is_admin,
        'admin_session_expires_in': remaining
    }
