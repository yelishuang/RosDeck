"""
Authentication endpoints for handling user and admin login flows.
"""
from fastapi import APIRouter, Request, Response, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import pam
import logging
import time
from app.deps.rate_limit import rate_limiter
from app.deps.csrf import csrf_protection
from app.deps.admin_auth import admin_auth
from app.services.admin_privileged import verify_root_password

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["认证"])


# ==================== Data models ====================

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    message: str = ""
    redirect: str = "../index.html"
    code: str = ""


class AdminVerifyRequest(BaseModel):
    password: str


class AdminVerifyResponse(BaseModel):
    success: bool
    message: str
    session_expires_in: int = 0


# ==================== Helper utilities ====================

def authenticate_linux_user(username: str, password: str) -> bool:
    """
    Validate user credentials against the host PAM stack.
    """
    try:
        p = pam.pam()
        return p.authenticate(username, password)
    except Exception as e:
        logger.error(f"PAM authentication error: {e}")
        return False


# ==================== Route handlers ====================

@router.post(
    "/login",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(rate_limiter.check_rate_limit)
    ]
)
async def login(body: LoginRequest, response: Response):
    """
    Authenticate a RosDeck user via Linux system credentials and issue a session cookie.

    The front-end expects:
    - Success: HTTP 200 with {ok: true, redirect: "../index.html"}
    - Failure: HTTP 401 with {ok: false, code: "AUTH_INVALID", message: "..."}
    """
    username = body.username.strip()
    password = body.password.strip()
    
    # Basic payload validation.
    if not username or not password:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "code": "AUTH_INVALID",
                "message": "用户名或密码不能为空"
            }
        )
    
    # Authenticate against the local PAM stack.
    if not authenticate_linux_user(username, password):
        logger.warning(f"Failed login attempt for user: {username}")
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "code": "AUTH_INVALID",
                "message": "用户名或密码错误"
            }
        )
    
    logger.info(f"User {username} logged in successfully")
    
    # Issue a session cookie with constrained browser access.
    session_id = f"session_{username}_{int(time.time())}"
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,      # Prevent JavaScript access to mitigate XSS.
        secure=False,       # HTTP during development; enforce True behind HTTPS.
        samesite="strict",  # Strict mode to reinforce CSRF protections.
        max_age=3600 * 24,  # 24-hour session lifetime.
        path="/"
    )
    
    return LoginResponse(
        ok=True,
        message="登录成功",
        redirect="../index.html"
    )


@router.post("/logout")
async def logout(response: Response):
    """
    Clear user-facing session cookies.
    """
    # Remove the regular user session cookie.
    response.delete_cookie(key="session_id", path="/")
    
    # Remove any admin session cookie.
    response.delete_cookie(key="admin_session_id", path="/")
    
    return {"ok": True, "message": "登出成功"}


@router.post(
    "/verify-admin",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(rate_limiter.check_rate_limit)
    ]
)
async def verify_admin(body: AdminVerifyRequest, response: Response):
    """
    Validate admin privileges by confirming the root password and minting an admin session.
    """
    password = body.password.strip()
    
    if not password:
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "message": "密码不能为空"
            }
        )
    
    # Defer to the privileged helper to verify the root password.
    if not verify_root_password(password):
        logger.warning(f"Failed admin verification attempt")
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "message": "密码错误"
            }
        )
    
    # Create an admin session with the configured timeout.
    admin_session_id = admin_auth.create_session("root")
    session_expires_in = admin_auth.session_timeout
    
    logger.info(f"Admin verification successful, session created: {admin_session_id}")
    
    # Persist the admin session cookie.
    response.set_cookie(
        key="admin_session_id",
        value=admin_session_id,
        httponly=True,
        secure=False,
        samesite="strict",
        max_age=session_expires_in,
        path="/"
    )
    
    return AdminVerifyResponse(
        success=True,
        message="验证成功",
        session_expires_in=session_expires_in
    )


@router.post(
    "/admin-logout",
    dependencies=[
        Depends(csrf_protection.validate_token)
    ]
)
async def admin_logout(request: Request, response: Response):
    """
    Terminate the admin session and clear the cookie.
    """
    admin_session_id = request.cookies.get("admin_session_id")
    if admin_session_id:
        admin_auth.revoke_session(admin_session_id)
    response.delete_cookie(key="admin_session_id", path="/")
    logger.info("Admin session revoked via /admin-logout")
    return {"success": True, "message": "管理员模式已退出"}
