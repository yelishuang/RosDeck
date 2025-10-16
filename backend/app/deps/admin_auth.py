"""
Admin privilege validation utilities.
"""
from fastapi import Request, HTTPException
import time
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class AdminAuthManager:
    """Manage elevated administrator sessions for privileged routes."""
    
    def __init__(self, session_timeout: int = 1800):
        """Initialise the manager with a session timeout in seconds (default 30 minutes)."""
        self.session_timeout = session_timeout
        self.admin_sessions: Dict[str, float] = {}  # session_id -> expiry timestamp
    
    def create_session(self, username: str) -> str:
        """Create a new admin session and return its identifier."""
        session_id = f"admin_{username}_{int(time.time())}"
        expire_time = time.time() + self.session_timeout
        self.admin_sessions[session_id] = expire_time
        
        logger.info(f"Created admin session {session_id} with TTL {self.session_timeout} seconds")
        return session_id
    
    def validate_session(self, session_id: str) -> bool:
        """Return True when the session exists and has not expired."""
        if session_id not in self.admin_sessions:
            return False
        
        expire_time = self.admin_sessions[session_id]
        current_time = time.time()
        
        if current_time > expire_time:
            # Evict expired sessions immediately.
            del self.admin_sessions[session_id]
            logger.warning(f"Admin session expired: {session_id}")
            return False
        
        return True
    
    def get_remaining_time(self, session_id: str) -> int:
        """Return the remaining lifetime of a session in seconds."""
        if session_id not in self.admin_sessions:
            return 0
        
        expire_time = self.admin_sessions[session_id]
        remaining = int(expire_time - time.time())
        return max(remaining, 0)
    
    def revoke_session(self, session_id: str):
        """Revoke an admin session explicitly."""
        if session_id in self.admin_sessions:
            del self.admin_sessions[session_id]
            logger.info(f"Revoked admin session: {session_id}")
    
    def cleanup_expired_sessions(self):
        """Remove any expired sessions from the in-memory store."""
        current_time = time.time()
        expired_sessions = [
            sid for sid, expire_time in self.admin_sessions.items()
            if current_time > expire_time
        ]
        
        for sid in expired_sessions:
            del self.admin_sessions[sid]
        
        if expired_sessions:
            logger.info(f"Cleaned {len(expired_sessions)} expired admin sessions")
    
    async def require_admin(self, request: Request):
        """FastAPI dependency that enforces admin authentication."""
        # Read the admin session identifier from cookies.
        admin_session_id = request.cookies.get("admin_session_id")
        
        if not admin_session_id:
            raise HTTPException(
                status_code=403,
                detail={"message": "需要管理员权限", "code": "ADMIN_REQUIRED"}
            )
        
        # Validate the associated session before proceeding.
        if not self.validate_session(admin_session_id):
            raise HTTPException(
                status_code=403,
                detail={"message": "管理员 session 已过期,请重新验证", "code": "ADMIN_SESSION_EXPIRED"}
            )
        
        # Opportunistically clean up any expired entries.
        self.cleanup_expired_sessions()


# Shared singleton instance (30-minute session TTL).
admin_auth = AdminAuthManager(session_timeout=1800)


def extract_username_from_session(session_id: str) -> str:
    """
    Extract the username component from a session identifier.

    Expected format: ``session_{username}_{timestamp}``. The last underscore acts as the
    delimiter so usernames may themselves contain underscores.
    """
    prefix = "session_"
    if not session_id.startswith(prefix):
        raise ValueError("session_id 格式无效: 缺少前缀")

    # After stripping the prefix, treat the last underscore as the timestamp separator.
    username_part = session_id[len(prefix):]
    username, separator, ts = username_part.rpartition("_")

    if not separator:
        raise ValueError("session_id 格式无效: 缺少时间戳分隔符")

    if not username:
        raise ValueError("session_id 格式无效: 用户名为空")

    if not ts.isdigit():
        raise ValueError("session_id 格式无效: 时间戳不是数字")

    return username


async def get_current_username(request: Request) -> str:
    """FastAPI dependency that returns the username associated with the session cookie."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(
            status_code=401,
            detail={"message": "未登录或会话已过期", "code": "AUTH_REQUIRED"}
        )

    try:
        username = extract_username_from_session(session_id)
    except ValueError as exc:
        logger.warning(f"Invalid session_id detected: {session_id} ({exc})")
        raise HTTPException(
            status_code=401,
            detail={"message": "会话无效, 请重新登录", "code": "AUTH_INVALID_SESSION"}
        ) from exc

    return username
