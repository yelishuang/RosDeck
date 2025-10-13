"""
FastAPI 主应用
"""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import logging

# 导入路由
from app.routes import auth
from app.routes import system
from app.routes import ros
from app.routes import ros_comm
from app.routes import ros_ai
from app.routes import device
from app.routes import files
from app.routes import terminal
from app.routes import network
from app.routes import logs
from app.routes import storage
from app.routes import runtime
from app.routes import ros_config

# 导入依赖
from app.deps.csrf import csrf_protection


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 创建应用
app = FastAPI(title="RosDeck Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1221",      # Nginx 前端地址
        "http://127.0.0.1:1221",
    ],
    allow_credentials=True,          
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

# 请求日志中间件
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"{request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"Response status: {response.status_code}")
    return response

# 注册路由
app.include_router(auth.router)
app.include_router(system.router)
app.include_router(ros.router)
app.include_router(ros_comm.router)
app.include_router(ros_ai.router)
app.include_router(device.router)
app.include_router(files.router)
app.include_router(terminal.router)
app.include_router(network.router)
app.include_router(logs.router)
app.include_router(storage.router)
app.include_router(runtime.router)
app.include_router(ros_config.router)

# 埋点接口
@app.post("/api/metrics")
async def collect_metrics(request: Request):
    try:
        data = await request.json()
        event = data.get('event')
        
        # 只记录关键操作,不记录页面浏览
        if event in ['login_success', 'login_failed', 'file_uploaded', 'command_executed']:
            logger.warning(f"AUDIT: {event} from {request.client.host} - {data}")
        
        return {"ok": True}
    except:
        return {"ok": False}

# 健康检查
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "csrf_enabled": True}

@app.get("/api/csrf-token")
async def issue_csrf_token():
    token = csrf_protection.get_token()
    if not token:
        token = csrf_protection.generate_token()
    return {"token": token}

# 根路径
@app.get("/")
async def root():
    return {"message": "RosDeck Backend API", "version": "0.1.0"}

# 登录页面
@app.get("/auth/login.html", response_class=HTMLResponse)
async def serve_login_page():
    """
    返回登录页面,注入 CSRF Token
    """
    # 读取 Nginx 部署的静态文件
    login_html_path = Path("/usr/share/nginx/html/rosdeck/auth/login.html")
    
    if not login_html_path.exists():
        return HTMLResponse(content="<h1>Login page not found</h1>", status_code=404)
    
    # 生成 CSRF Token
    csrf_token = csrf_protection.generate_token()
    
    # 读取 HTML 并替换占位符
    html_content = login_html_path.read_text(encoding="utf-8")
    html_content = html_content.replace(
        '<!-- <meta name="csrf-token" content="YOUR_TOKEN_HERE"> -->',
        f'<meta name="csrf-token" content="{csrf_token}">'
    )
    
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",  # 只监听本地,外部通过 Nginx 访问
        port=4162,         # 使用 4162 端口
        reload=True
    )
