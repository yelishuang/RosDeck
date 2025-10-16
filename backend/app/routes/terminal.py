"""
WebSocket endpoints providing interactive terminal access.
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
    Manage authenticated WebSocket terminal sessions with optional admin privileges.

    Authentication is derived from cookies:
    - session_id: regular user session, resolves to a username.
    - admin_session_id: marks the session as elevated when present and valid.
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

    # Determine whether the user holds an active admin session.
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

    # Generate a unique identifier for tracking the WebSocket session.
    ws_session_id = str(uuid.uuid4())

    # Allocate a terminal session bound to the user context.
    session = terminal_manager.create_session(ws_session_id, username, is_admin)

    # Initialize the pseudo-terminal backing the session.
    pty_started = await session.start_pty()
    if not pty_started:
        await websocket.send_json({
            'type': 'error',
            'message': '无法启动终端会话'
        })
        await websocket.close(code=1011)
        return

    # Send session metadata so the client can render status.
    await websocket.send_json({
        'type': 'session_info',
        'username': username,
        'is_admin': is_admin,
        'admin_session_expires_in': admin_remaining
    })

    # Emit a welcome banner.
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

    # Ensure the background cleanup coroutine is running.
    await terminal_manager.start_cleanup_task()

    # Spawn a reader task that streams PTY output.
    async def read_from_pty():
        """Read PTY output and forward it to the client."""
        while True:
            try:
                if session.pty and session.pty.isalive():
                    # Pull data from the pseudo-terminal.
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
                    # Exit if the PTY process has terminated.
                    await websocket.send_json({
                        'type': 'error',
                        'message': '终端会话已结束'
                    })
                    break
            except Exception as e:
                logger.error(f"Error reading from PTY: {e}")
                break

    # Begin streaming PTY output.
    read_task = asyncio.create_task(read_from_pty())

    try:
        # Handle inbound client messages.
        while True:
            try:
                # Receive a message frame from the client.
                message = await websocket.receive_text()
                data = json.loads(message)
                logger.debug("WS %s <- client message: %s", ws_session_id, message)

                msg_type = data.get('type')

                if msg_type == 'input':
                    # Forward interactive input to the PTY.
                    input_data = data.get('data', '')
                    await session.write(input_data)

                elif msg_type == 'resize':
                    # Resize the PTY to match the client dimensions.
                    rows = data.get('rows', 24)
                    cols = data.get('cols', 80)
                    session.resize(rows, cols)
                    logger.debug("WS %s resize -> rows=%s cols=%s", ws_session_id, rows, cols)

                elif msg_type == 'command_check':
                    # Validate whether the requested command is allowed.
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
                        # Persist the command for session history.
                        session.add_command(command)
                        logger.debug("WS %s command allowed: %s", ws_session_id, command)
                        await websocket.send_json({
                            'type': 'command_allowed',
                            'command': command
                        })

                elif msg_type == 'ping':
                    # Respond to heartbeat probes.
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
        # Perform cleanup regardless of exit path.
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
    Return the command history for the active terminal session.
    """
    # Return history from any live session owned by the user.
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
    Expose session metadata including admin status and remaining elevation time.
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
