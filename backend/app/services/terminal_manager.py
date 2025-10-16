"""
Terminal management service responsible for PTY sessions, command filtering, and privilege checks.
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
    """Represents a single PTY-backed terminal session."""

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
        """Refresh the last-activity timestamp."""
        self.last_activity = datetime.now()

    def add_command(self, command: str):
        """Append a command to the in-memory history."""
        if command.strip():
            self.command_history.append({
                'command': command,
                'timestamp': datetime.now().isoformat()
            })
            # Enforce the maximum history length.
            if len(self.command_history) > self.max_history:
                self.command_history = self.command_history[-self.max_history:]

    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """Return True when the session has been idle beyond the timeout window."""
        return datetime.now() - self.last_activity > timedelta(minutes=timeout_minutes)

    async def start_pty(self) -> bool:
        """Spawn an interactive shell PTY for the associated user."""
        try:
            # Resolve user account details.
            user_info = pwd.getpwnam(self.username)

            # Prepare an environment suitable for interactive terminals.
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

            # Launch the shell using the current user context.
            # Note: ptyprocess cannot switch users directly without sudo or similar tooling.
            shell = user_info.pw_shell or '/bin/bash'

            self.pty = ptyprocess.PtyProcess.spawn(
                [shell, '-i'],  # Interactive shell.
                env=env,
                cwd=user_info.pw_dir,
                dimensions=(24, 80)  # Default terminal geometry.
            )

            logger.info(f"PTY started for user {self.username}, session {self.session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to start PTY for {self.username}: {e}")
            return False

    def resize(self, rows: int, cols: int):
        """Adjust the PTY terminal size."""
        if self.pty and self.pty.isalive():
            try:
                self.pty.setwinsize(rows, cols)
            except Exception as e:
                logger.error(f"Failed to resize terminal: {e}")

    async def write(self, data: str):
        """Write data to the PTY."""
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
        """Read data from the PTY with a configurable timeout."""
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
        """Terminate the session and its PTY."""
        if self.pty and self.pty.isalive():
            try:
                self.pty.terminate(force=True)
                logger.info(f"Terminal session {self.session_id} closed")
            except Exception as e:
                logger.error(f"Error closing PTY: {e}")


class TerminalManager:
    """Coordinates PTY sessions and enforces command safety policies."""

    # Dangerous command blacklist expressed as regular expressions.
    BLACKLIST_PATTERNS = [
        r'rm\s+(-[rf]*\s+)*/',  # rm -rf /.
        r'mkfs',  # Formatting utilities.
        r'dd\s+.*of=/dev/(sd|hd|nvme)',  # Hazardous dd activity.
        r':\(\)\{.*\|.*&\s*\};:',  # Fork bomb signature.
        r'chmod\s+(-R\s+)?[0-7]{3,4}\s+/',  # Recursive chmod on root.
        r'chown\s+(-R\s+)?\w+\s+/',  # Recursive ownership change on root.
        r'>\s*/dev/(sd|hd|nvme)',  # Direct raw writes to block devices.
    ]

    # Command patterns restricted to admin sessions.
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
        """Create a new terminal session, replacing any existing session for the user."""
        # Close any existing session for this user.
        for sid, session in list(self.sessions.items()):
            if session.username == username:
                # Close the previous session before creating a new one.
                session.close()
                del self.sessions[sid]
                logger.info(f"Closed existing session for user {username}")

        session = TerminalSession(session_id, username, is_admin)
        self.sessions[session_id] = session
        logger.info(f"Created terminal session {session_id} for user {username} (admin: {is_admin})")
        return session

    def get_session(self, session_id: str) -> Optional[TerminalSession]:
        """Return the session associated with the given identifier."""
        return self.sessions.get(session_id)

    def close_session(self, session_id: str):
        """Close and remove the referenced session if it exists."""
        session = self.sessions.pop(session_id, None)
        if session:
            session.close()

    def check_command_allowed(self, command: str, is_admin: bool) -> tuple[bool, Optional[str]]:
        """
        Determine whether the provided command is allowed to execute.

        Returns a tuple of (allowed, reason).
        """
        command = command.strip()

        if not command:
            return True, None

        # Enforce blacklist rules first.
        for pattern in self.BLACKLIST_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"命令被黑名单禁止: 匹配模式 '{pattern}'"

        # Restrict privileged commands when the user lacks admin status.
        if not is_admin:
            for pattern in self.ADMIN_ONLY_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    return False, f"该命令需要管理员权限"

        return True, None

    async def start_cleanup_task(self):
        """Launch the background task that prunes expired sessions."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_sessions())

    async def _cleanup_expired_sessions(self):
        """Periodically close sessions that have exceeded the idle timeout."""
        while True:
            try:
                await asyncio.sleep(60)  # Check once per minute.

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
        """Cancel the background cleanup task if it is active."""
        if self._cleanup_task:
            self._cleanup_task.cancel()

    def close_all_sessions(self):
        """Force-close every active session."""
        for session_id in list(self.sessions.keys()):
            self.close_session(session_id)


# Shared terminal manager instance.
terminal_manager = TerminalManager()
