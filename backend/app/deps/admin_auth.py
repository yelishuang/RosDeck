"""
管理员权限验证
"""
from fastapi import Request, HTTPException
import time
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class AdminAuthManager:
    """管理员认证管理器"""
    
    def __init__(self, session_timeout: int = 1800):
        """
        Args:
            session_timeout: Session 超时时间(秒),默认 30 分钟
        """
        self.session_timeout = session_timeout
        self.admin_sessions: Dict[str, float] = {}  # {session_id: expire_time}
    
    def create_session(self, username: str) -> str:
        """
        创建管理员 session
        
        Returns:
            session_id
        """
        session_id = f"admin_{username}_{int(time.time())}"
        expire_time = time.time() + self.session_timeout
        self.admin_sessions[session_id] = expire_time
        
        logger.info(f"创建管理员 session: {session_id}, 有效期: {self.session_timeout}秒")
        return session_id
    
    def validate_session(self, session_id: str) -> bool:
        """验证 session 是否有效"""
        if session_id not in self.admin_sessions:
            return False
        
        expire_time = self.admin_sessions[session_id]
        current_time = time.time()
        
        if current_time > expire_time:
            # Session 过期,清理
            del self.admin_sessions[session_id]
            logger.warning(f"管理员 session 已过期: {session_id}")
            return False
        
        return True
    
    def get_remaining_time(self, session_id: str) -> int:
        """获取 session 剩余有效时间(秒)"""
        if session_id not in self.admin_sessions:
            return 0
        
        expire_time = self.admin_sessions[session_id]
        remaining = int(expire_time - time.time())
        return max(remaining, 0)
    
    def revoke_session(self, session_id: str):
        """撤销 session"""
        if session_id in self.admin_sessions:
            del self.admin_sessions[session_id]
            logger.info(f"撤销管理员 session: {session_id}")
    
    def cleanup_expired_sessions(self):
        """清理所有过期的 session"""
        current_time = time.time()
        expired_sessions = [
            sid for sid, expire_time in self.admin_sessions.items()
            if current_time > expire_time
        ]
        
        for sid in expired_sessions:
            del self.admin_sessions[sid]
        
        if expired_sessions:
            logger.info(f"清理了 {len(expired_sessions)} 个过期的管理员 session")
    
    async def require_admin(self, request: Request):
        """
        依赖注入: 要求管理员权限
        
        使用方式:
            @router.post("/power", dependencies=[Depends(admin_auth.require_admin)])
        """
        # 从 Cookie 获取 admin_session_id
        admin_session_id = request.cookies.get("admin_session_id")
        
        if not admin_session_id:
            raise HTTPException(
                status_code=403,
                detail={"message": "需要管理员权限", "code": "ADMIN_REQUIRED"}
            )
        
        # 验证 session
        if not self.validate_session(admin_session_id):
            raise HTTPException(
                status_code=403,
                detail={"message": "管理员 session 已过期,请重新验证", "code": "ADMIN_SESSION_EXPIRED"}
            )
        
        # 清理过期 session (顺便做)
        self.cleanup_expired_sessions()


# 全局实例
admin_auth = AdminAuthManager(session_timeout=1800)  # 30 分钟