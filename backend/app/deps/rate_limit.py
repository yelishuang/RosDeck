"""
简化版限流器（基于内存）
"""
from fastapi import Request, HTTPException
from datetime import datetime, timedelta
from collections import defaultdict

class RateLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts = defaultdict(list)  
    
    async def check_rate_limit(self, request: Request):
        """
        检查登录限流（依赖注入使用）
        """
        client_ip = request.client.host
        now = datetime.now()
        cutoff = now - timedelta(seconds=self.window_seconds)
        
        # 清理过期记录
        self.attempts[client_ip] = [
            t for t in self.attempts[client_ip] if t > cutoff
        ]
        
        # 检查是否超限
        if len(self.attempts[client_ip]) >= self.max_attempts:
            oldest = min(self.attempts[client_ip])
            retry_after = int((oldest + timedelta(seconds=self.window_seconds) - now).total_seconds())
            
            raise HTTPException(
                status_code=429,
                detail={"retryAfter": max(retry_after, 1)},
                headers={"Retry-After": str(max(retry_after, 1))}
            )
        
        # 记录本次尝试
        self.attempts[client_ip].append(now)

# 全局实例
rate_limiter = RateLimiter()