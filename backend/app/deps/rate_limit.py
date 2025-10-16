"""
Lightweight in-memory rate limiter for login attempts.
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
        """FastAPI dependency that enforces IP-based rate limiting."""
        client_ip = request.client.host
        now = datetime.now()
        cutoff = now - timedelta(seconds=self.window_seconds)
        
        # Remove any attempts outside the window.
        self.attempts[client_ip] = [
            t for t in self.attempts[client_ip] if t > cutoff
        ]
        
        # Deny the request when the maximum number of attempts is exceeded.
        if len(self.attempts[client_ip]) >= self.max_attempts:
            oldest = min(self.attempts[client_ip])
            retry_after = int((oldest + timedelta(seconds=self.window_seconds) - now).total_seconds())
            
            raise HTTPException(
                status_code=429,
                detail={"retryAfter": max(retry_after, 1)},
                headers={"Retry-After": str(max(retry_after, 1))}
            )
        
        # Track the current request.
        self.attempts[client_ip].append(now)

# Shared singleton instance.
rate_limiter = RateLimiter()
