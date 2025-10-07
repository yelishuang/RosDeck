"""
认证相关路由
"""
from fastapi import APIRouter, Request, Response, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import pam
import logging
from app.deps.rate_limit import rate_limiter
from app.deps.csrf import csrf_protection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["认证"])

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    ok: bool
    message: str = ""
    redirect: str = "../index.html"
    code: str = ""

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

@router.post("/login", dependencies=[Depends(csrf_protection.validate_token),Depends(rate_limiter.check_rate_limit)])
async def login(body: LoginRequest, response: Response):
    """
    登录接口 - 使用 Linux 系统账号验证
    前端期望：
    - 成功：200 + {ok:true, redirect:"../index.html"}
    - 失败：401 + {ok:false, code:"AUTH_INVALID", message:"..."}
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
        httponly=True,      # 防止 JavaScript 访问（防 XSS）
        secure=False,       # 开发环境 HTTP，生产环境改为 True
        samesite="strict",  # 防止 CSRF 攻击（与 CSRF Token 双重保护）
        max_age=3600 * 24,  # 24小时
        path="/"
    )
    
    return LoginResponse(
        ok=True,
        message="登录成功",
        redirect="../index.html"
    )

import time