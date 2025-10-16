"""
FastAPI application bootstrap for the RosDeck backend.
"""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import logging

# Application router modules registered below.
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

# Shared dependency providers.
from app.deps.csrf import csrf_protection


# Configure application-wide logging.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Instantiate the FastAPI application.
app = FastAPI(title="RosDeck Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1221",      # Public-facing frontend served by Nginx.
        "http://127.0.0.1:1221",
    ],
    allow_credentials=True,          
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

# Log every HTTP request and response status.
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"{request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"Response status: {response.status_code}")
    return response

# Register API routers.
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

# Lightweight audit endpoint for front-end events.
@app.post("/api/metrics")
async def collect_metrics(request: Request):
    try:
        data = await request.json()
        event = data.get('event')
        
        # Persist only security-relevant events; ignore passive visits.
        if event in ['login_success', 'login_failed', 'file_uploaded', 'command_executed']:
            logger.warning(f"AUDIT: {event} from {request.client.host} - {data}")
        
        return {"ok": True}
    except:
        return {"ok": False}

# Static health probe for uptime checks.
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "csrf_enabled": True}

@app.get("/api/csrf-token")
async def issue_csrf_token():
    token = csrf_protection.get_token()
    if not token:
        token = csrf_protection.generate_token()
    return {"token": token}

# Minimal root handler for quick connectivity probes.
@app.get("/")
async def root():
    return {"message": "RosDeck Backend API", "version": "0.1.0"}

# Serve the login page and inject CSRF metadata.
@app.get("/auth/login.html", response_class=HTMLResponse)
async def serve_login_page():
    """
    Return the login HTML page with an inline CSRF token.
    """
    # Read the login template directly from the Nginx-served directory.
    login_html_path = Path("/usr/share/nginx/html/rosdeck/auth/login.html")
    
    if not login_html_path.exists():
        return HTMLResponse(content="<h1>Login page not found</h1>", status_code=404)
    
    # Issue a fresh CSRF token for the session.
    csrf_token = csrf_protection.generate_token()
    
    # Inject the generated token into the HTML placeholder.
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
        host="127.0.0.1",  # Bind to localhost; external access is proxied via Nginx.
        port=4162,         # Default backend port.
        reload=True
    )
