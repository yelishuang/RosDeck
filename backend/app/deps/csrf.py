"""
CSRF 保护中间件
"""
from fastapi import Request, HTTPException, Header
from secrets import token_urlsafe
from typing import Optional

class CSRFProtection:
    def __init__(self):
        # 生产环境应使用 Redis，这里用内存存储
        self.tokens = {}
    
    def generate_token(self, identifier: str = "global") -> str:
        """生成 CSRF Token"""
        token = token_urlsafe(32)
        self.tokens[identifier] = token
        return token
    
    def get_token(self, identifier: str = "global") -> Optional[str]:
        """获取已生成的 Token"""
        return self.tokens.get(identifier)
    
    async def validate_token(
        self,
        request: Request,
        x_csrf_token: Optional[str] = Header(None, alias="X-CSRF-Token")
    ):
        """验证 CSRF Token（依赖注入使用）"""
        identifier = "global"
        expected_token = self.tokens.get(identifier)
        
        if not expected_token:
            raise HTTPException(status_code=403, detail="CSRF token not initialized")
        
        if not x_csrf_token or x_csrf_token != expected_token:
            raise HTTPException(status_code=403, detail="Invalid CSRF token")

# 全局实例
csrf_protection = CSRFProtection()