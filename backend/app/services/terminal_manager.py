"""
终端管理服务
负责 PTY 会话管理、命令黑名单检查、权限控制
"""
import os
import re
import pwd
import logging
from typing import Optional, Dict, Set
from datetime import datetime, timedelta
import asyncio
import ptyprocess

logger = logging.getLogger(__name__)


class TerminalSession:
    """单个终端会话"""

    def __init__(self, session_id: str, username: str, is_admin: bool = False):
        self.session_id = session_id
        self.username = username
        self.is_admin = is_admin
        self.pty: Optional[ptyprocess.PtyProcess] = None
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.command_history = []
        self.max_history = 100

    def update_activity(self):
        """更新最后活动时间"""
        self.last_activity = datetime.now()

    def add_command(self, command: str):
        """添加命令到历史记录"""
        if command.strip():
            self.command_history.append({
                'command': command,
                'timestamp': datetime.now().isoformat()
            })
            # 保持历史记录在限制内
            if len(self.command_history) > self.max_history:
                self.command_history = self.command_history[-self.max_history:]

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """检查会话是否超时"""
        return datetime.now() - self.last_activity > timedelta(minutes=timeout_minutes)

    async def start_pty(self) -> bool:
        """启动 PTY 进程"""
        try:
            # 获取用户信息
            user_info = pwd.getpwnam(self.username)

            # 设置环境变量
            env = os.environ.copy()
            env['TERM'] = 'xterm-256color'
            env['HOME'] = user_info.pw_dir
            env['USER'] = self.username
            env['LOGNAME'] = self.username
            env['SHELL'] = user_info.pw_shell or '/bin/bash'
            env.pop('VIRTUAL_ENV', None)
            env.pop('PYTHONHOME', None)
            env.pop('PS1', None)
            env.pop('PROMPT_COMMAND', None)
            env['PS1'] = r'[\u@\h \W]\$ '

            # 启动 shell（以当前用户身份，不切换用户）
            # 注意：ptyprocess 不支持直接切换用户，需要配合 sudo 或其他方式
            shell = user_info.pw_shell or '/bin/bash'

            self.pty = ptyprocess.PtyProcess.spawn(
                [shell, '-i'],  # 交互式 shell
                env=env,
                cwd=user_info.pw_dir,
                dimensions=(24, 80)  # 默认终端大小
            )

            logger.info(f"PTY started for user {self.username}, session {self.session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to start PTY for {self.username}: {e}")
            return False

    def resize(self, rows: int, cols: int):
        """调整终端大小"""
        if self.pty and self.pty.isalive():
            try:
                self.pty.setwinsize(rows, cols)
            except Exception as e:
                logger.error(f"Failed to resize terminal: {e}")

    async def write(self, data: str):
        """写入数据到 PTY"""
        if self.pty and self.pty.isalive():
            try:
                logger.debug("TTY write [%s]: %r", self.username, data)
                payload = data.encode('utf-8', errors='ignore') if isinstance(data, str) else data
                self.pty.write(payload)
                self.update_activity()
            except Exception as e:
                logger.error(f"Failed to write to PTY: {e}")
                raise

    async def read(self, timeout: float = 0.1) -> Optional[str]:
        """从 PTY 读取数据"""
        if not self.pty or not self.pty.isalive():
            return None

        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, self.pty.read, 4096)
            if data:
                self.update_activity()
                decoded = data.decode('utf-8', errors='ignore')
                logger.debug("TTY read  [%s]: %r", self.username, decoded)
                return decoded
        except EOFError:
            return None
        except Exception as e:
            logger.error(f"Failed to read from PTY: {e}")
            return None

    def close(self):
        """关闭会话"""
        if self.pty and self.pty.isalive():
            try:
                self.pty.terminate(force=True)
                logger.info(f"Terminal session {self.session_id} closed")
            except Exception as e:
                logger.error(f"Error closing PTY: {e}")


class TerminalManager:
    """终端管理器"""

    # 危险命令黑名单（正则表达式）
    BLACKLIST_PATTERNS = [
        r'rm\s+(-[rf]*\s+)*/',  # rm -rf /
        r'mkfs',  # 格式化文件系统
        r'dd\s+.*of=/dev/(sd|hd|nvme)',  # 危险的 dd 操作
        r':\(\)\{.*\|.*&\s*\};:',  # Fork bomb
        r'chmod\s+(-R\s+)?[0-7]{3,4}\s+/',  # 修改根目录权限
        r'chown\s+(-R\s+)?\w+\s+/',  # 修改根目录所有权
        r'>\s*/dev/(sd|hd|nvme)',  # 直接写入磁盘设备
    ]

    # 管理员才能执行的命令模式
    ADMIN_ONLY_PATTERNS = [
        r'sudo',
        r'su\s',
        r'systemctl',
        r'service\s',
        r'reboot',
        r'shutdown',
        r'init\s',
        r'telinit',
        r'poweroff',
        r'halt',
    ]

    def __init__(self):
        self.sessions: Dict[str, TerminalSession] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    def create_session(self, session_id: str, username: str, is_admin: bool = False) -> TerminalSession:
        """创建新会话"""
        # 检查是否已存在该用户的会话
        for sid, session in list(self.sessions.items()):
            if session.username == username:
                # 关闭旧会话
                session.close()
                del self.sessions[sid]
                logger.info(f"Closed existing session for user {username}")

        session = TerminalSession(session_id, username, is_admin)
        self.sessions[session_id] = session
        logger.info(f"Created terminal session {session_id} for user {username} (admin: {is_admin})")
        return session

    def get_session(self, session_id: str) -> Optional[TerminalSession]:
        """获取会话"""
        return self.sessions.get(session_id)

    def close_session(self, session_id: str):
        """关闭会话"""
        session = self.sessions.pop(session_id, None)
        if session:
            session.close()

    def check_command_allowed(self, command: str, is_admin: bool) -> tuple[bool, Optional[str]]:
        """
        检查命令是否允许执行
        返回: (是否允许, 拒绝原因)
        """
        command = command.strip()

        if not command:
            return True, None

        # 检查黑名单
        for pattern in self.BLACKLIST_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"命令被黑名单禁止: 匹配模式 '{pattern}'"

        # 非管理员检查受限命令
        if not is_admin:
            for pattern in self.ADMIN_ONLY_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    return False, f"该命令需要管理员权限"

        return True, None

    async def start_cleanup_task(self):
        """启动会话清理任务"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())

    async def _cleanup_expired_sessions(self):
        """定期清理过期会话"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次

                expired_sessions = []
                for session_id, session in self.sessions.items():
                    if session.is_expired():
                        expired_sessions.append(session_id)

                for session_id in expired_sessions:
                    logger.info(f"Closing expired session: {session_id}")
                    self.close_session(session_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")

    def stop_cleanup_task(self):
        """停止清理任务"""
        if self._cleanup_task:
            self._cleanup_task.cancel()

    def close_all_sessions(self):
        """关闭所有会话"""
        for session_id in list(self.sessions.keys()):
            self.close_session(session_id)


# 全局终端管理器实例
terminal_manager = TerminalManager()
