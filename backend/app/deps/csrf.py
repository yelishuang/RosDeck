"""
CSRF protection helper backed by an in-memory token store.
"""
from fastapi import Request, HTTPException, Header
from secrets import token_urlsafe
from typing import Optional

class CSRFProtection:
    def __init__(self):
        # In production this should leverage a shared store (e.g. Redis); in-memory is for dev use.
        self.tokens = {}
    
    def generate_token(self, identifier: str = "global") -> str:
        """Generate a CSRF token for the provided identifier."""
        token = token_urlsafe(32)
        self.tokens[identifier] = token
        return token
    
    def get_token(self, identifier: str = "global") -> Optional[str]:
        """Retrieve a previously generated token if present."""
        return self.tokens.get(identifier)
    
    async def validate_token(
        self,
        request: Request,
        x_csrf_token: Optional[str] = Header(None, alias="X-CSRF-Token")
    ):
        """Validate that the request includes the expected CSRF token."""
        identifier = "global"
        expected_token = self.tokens.get(identifier)
        
        if not expected_token:
            raise HTTPException(status_code=403, detail="CSRF token not initialized")
        
        if not x_csrf_token or x_csrf_token != expected_token:
            raise HTTPException(status_code=403, detail="Invalid CSRF token")

# Shared singleton instance.
csrf_protection = CSRFProtection()
