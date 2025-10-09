"""
认证相关路由
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


# ==================== 数据模型 ====================

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


# ==================== 辅助函数 ====================

def authenticate_linux_user(username: str, password: str) -> bool:
    """
    使用 Linux PAM 验证用户
    """
    try:
        p = pam.pam()
        return p.authenticate(username, password)
    except Exception as e:
        logger.error(f"PAM authentication error: {e}")
        return False


# ==================== 路由处理器 ====================

@router.post(
    "/login",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(rate_limiter.check_rate_limit)
    ]
)
async def login(body: LoginRequest, response: Response):
    """
    登录接口 - 使用 Linux 系统账号验证
    
    前端期望:
    - 成功: 200 + {ok:true, redirect:"../index.html"}
    - 失败: 401 + {ok:false, code:"AUTH_INVALID", message:"..."}
    """
    username = body.username.strip()
    password = body.password.strip()
    
    # 基本验证
    if not username or not password:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "code": "AUTH_INVALID",
                "message": "用户名或密码不能为空"
            }
        )
    
    # 使用 Linux PAM 验证
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
    
    # 设置 HttpOnly + SameSite Cookie
    session_id = f"session_{username}_{int(time.time())}"
    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,      # 防止 JavaScript 访问(防 XSS)
        secure=False,       # 开发环境 HTTP,生产环境改为 True
        samesite="strict",  # 防止 CSRF 攻击(与 CSRF Token 双重保护)
        max_age=3600 * 24,  # 24小时
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
    登出接口
    """
    # 清除普通用户 session
    response.delete_cookie(key="session_id", path="/")
    
    # 清除管理员 session
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
    验证管理员权限
    
    使用 root 用户密码验证
    验证成功后创建管理员 session(有效期 30 分钟)
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
    
    # 使用特权助手验证 root 用户
    if not verify_root_password(password):
        logger.warning(f"Failed admin verification attempt")
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "message": "密码错误"
            }
        )
    
    # 创建管理员 session
    admin_session_id = admin_auth.create_session("root")
    session_expires_in = admin_auth.session_timeout
    
    logger.info(f"Admin verification successful, session created: {admin_session_id}")
    
    # 设置管理员 Cookie
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
