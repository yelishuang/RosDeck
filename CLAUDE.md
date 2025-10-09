# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RosDeck is an open-source LAN console for monitoring and managing development boards or edge devices running ROS 2. It provides lightweight server management capabilities (disk/network monitoring, file transfer, terminal) with plans to integrate LLM-based interactions with ROS resources.

**Tech Stack:**
- Backend: FastAPI (Python 3.11+) with psutil for system monitoring, PAM for authentication, optional rclpy for ROS integration
- Frontend: jQuery-based modular architecture with static HTML/CSS/JS served via Nginx
- Deployment: Nginx reverse proxy (port 1221) → FastAPI backend (port 4162)
- Privileged Operations: setuid C helpers for PAM authentication and system control (reboot/shutdown)

## Development Commands

### Start Full Stack (requires sudo)
```bash
scripts/run_dev.sh
```
- Compiles and installs privileged helpers to `/usr/local/libexec/`
- Syncs frontend to `/usr/share/nginx/html/rosdeck`
- Configures Nginx at `/etc/nginx/conf.d/rosdeck.conf`
- Starts backend at http://127.0.0.1:4162
- Logs to `/var/log/rosdeck/backend.log`
- Frontend accessible at http://localhost:1221

### Backend Only (development mode)
```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 4162
```

### Stop Services
```bash
scripts/stop.sh
```

### Health Check
```bash
curl -fsS http://127.0.0.1:4162/api/health
```

### View Logs
```bash
tail -f /var/log/rosdeck/backend.log
```

### Manual Frontend Testing
```bash
cd html
python -m http.server 8000
```

## Architecture

### Backend Structure (`backend/app/`)

**Routes** (`routes/`):
- `auth.py` - PAM-based Linux authentication, admin verification, CSRF protection, rate limiting
- `system.py` - System metrics via SystemMonitor, power operations (reboot/shutdown)
- `device.py` - Host info (hostname, architecture, IP, openEuler detection)
- `ros.py` - ROS 2 metrics (nodes/topics/services count, stability) via ROSMonitor
- `files.py` - File operations (list/upload/download/delete) with user path restrictions

**Services** (`services/`):
- `system_monitor.py` - psutil wrapper with 2-second cache
- `ros_monitor.py` - Background thread running rclpy node; falls back to defaults if rclpy unavailable
- `admin_privileged.py` - Subprocess wrapper for `/usr/local/libexec/rosdeck-control-helper`

**Dependencies** (`deps/`):
- `csrf_protection` - In-memory CSRF token storage (needs Redis/DB for multi-instance)
- `admin_auth` - In-memory admin session management (30min TTL)
- `rate_limiter` - IP-based rate limiting (5 attempts/5min)

**Entry Point**: `main.py` (port 4162)
- CORS configured for `http://localhost:1221`
- Logs all requests via middleware
- Serves login page with injected CSRF token at `/auth/login.html`

### Frontend Structure (`html/`)

**Module System**:
- Each module has `modules/{name}/index.html`, `main.js`, `style.css`
- `index.js` orchestrates module loading via `loadModule(modulePath)`
- Modules expose `window.moduleInit()` and `window.moduleCleanup()` lifecycle hooks
- Admin mode changes broadcast via `rosdeck:admin-mode-change` event

**Main Controller** (`index.js`):
- Status ribbon updates every 5s (CPU/memory/disk/network from `/api/system/status`)
- Device info updates every 60s (from `/api/device/info`)
- CSRF token managed via sessionStorage (`rosdeck_csrf_token`)
- Admin mode verification/logout with toast notifications

**Existing Modules**:
- `overview` - Dashboard with ROS metrics polling
- `file-transfer` - Directory listing, upload (50MB limit), download, delete; normal users restricted to `~`, admins have full access with warnings for sensitive paths
- `network`, `storage`, `logs`, `runtime`, `terminal` - Partially implemented
- `ros/*` - ROS-specific pages (overview, communication, operations, ai-commander)

### Privileged Helpers (`privileged/`)

**Authentication Helper** (`rosdeck_auth_helper.c`):
- Validates user credentials via PAM
- Installed at `/usr/local/libexec/rosdeck-auth-helper` with setuid root

**Control Helper** (`src/rosdeck_control_helper.c`):
- Executes system commands (reboot, shutdown)
- Installed at `/usr/local/libexec/rosdeck-control-helper` with setuid root
- Legacy symlink at `/usr/local/libexec/rosdeck-power-helper`

**Security**: Always review input validation and sanitization when modifying helpers.

### Deployment Flow

1. `scripts/run_dev.sh` compiles C helpers if source is newer than binary
2. Syncs `html/` → `/usr/share/nginx/html/rosdeck/`
3. Copies `nginx/rosdeck.nginx.conf` → `/etc/nginx/conf.d/rosdeck.conf`
4. Installs Python deps from `backend/requirements.txt` into `.venv`
5. Starts backend via `nohup uvicorn` with PID file at `/run/rosdeck-backend.pid`
6. Performs health check with 5 retries

## Key Design Patterns

### Frontend Module Pattern
```javascript
// modules/{name}/main.js
window.moduleInit = function() {
    // Setup code, event listeners, initial data fetch
};

window.moduleCleanup = function() {
    // Clear intervals, remove listeners, cleanup state
};
```

### Admin Mode Workflow
1. User clicks admin toggle → prompts for root password
2. POST to `/api/auth/verify-admin` with CSRF token
3. Backend validates via PAM helper, creates 30min admin session
4. Frontend activates admin UI, broadcasts `rosdeck:admin-mode-change`
5. Logout POST to `/api/auth/admin-logout` clears session

### CSRF Protection
- Backend generates token stored in-memory
- Login page receives token via meta tag injection
- Frontend stores in sessionStorage and sends via `X-CSRF-Token` header
- All POST/PUT/DELETE requests require valid token

### ROS Integration
- `ROSMonitor` runs `rclpy.spin()` in daemon thread
- Gracefully degrades if rclpy import fails (logs warning, returns default values)
- Stats cached for performance

## Important Constraints

### Security
- Never commit production configs or secrets
- Audit privileged helper changes carefully (they run as root)
- CSRF tokens and admin sessions are in-memory (won't survive restarts)
- Consider Redis/DB for production multi-instance deployments

### Frontend Conventions
- 4-space indentation, camelCase functions
- Double quotes for strings
- No emojis in UI unless explicitly requested
- Module names map to paths: `'file-transfer'` → `modules/file-transfer/`

### Backend Conventions
- Follow PEP 8 / Black style
- Use type hints where helpful
- Log at appropriate levels (INFO for normal ops, WARNING for degraded state, ERROR for failures)
- Routes return `{"success": bool, "message": str, ...}` for consistency

### File Operations
- Normal users restricted to their home directory
- Admins can access any path but receive warnings for sensitive directories (`/etc`, `/root`, `/var`)
- 50MB upload limit enforced in `files.py`

## Testing

Currently no automated tests exist. When adding features:
- Create `backend/tests/test_{module}.py` using pytest + FastAPI TestClient
- Mock privileged operations (`subprocess.run`, PAM calls)
- Frontend: provide manual test checklist in PR (browser, steps, expected results)

## ROS Environment

- If `rclpy` is available in the environment, backend will discover and monitor ROS 2 nodes/topics/services
- Check `ROS_DISTRO` environment variable if metrics appear missing
- Backend logs warnings if ROS integration fails to initialize

## Common Issues

**Backend won't start:**
- Check if port 4162 is in use: `ss -ltnp | grep 4162`
- Review logs: `tail -f /var/log/rosdeck/backend.log`
- Ensure Python venv activated and deps installed

**Frontend loads but APIs fail:**
- Verify Nginx proxy config at `/etc/nginx/conf.d/rosdeck.conf`
- Check CSRF token in browser console (should be in sessionStorage)
- Confirm backend health: `curl http://127.0.0.1:4162/api/health`

**Privileged operations fail:**
- Ensure helpers compiled and setuid: `ls -la /usr/local/libexec/rosdeck-*`
- Check PAM configuration on the system
- Review backend logs for subprocess errors

**ROS metrics show zeros:**
- Verify `rclpy` is importable in backend venv: `python -c "import rclpy"`
- Check `ROS_DISTRO` and source ROS setup scripts before starting backend
- Look for ROSMonitor warnings in backend logs

## Future Directions

See `AGENTS.md` section 13 for roadmap. Key themes:
1. Complete system monitoring (temperature, power, disk partitions)
2. Network management (realtime bandwidth, Wi-Fi/Ethernet switching)
3. Terminal and file transfer frontend completion
4. ROS graph visualization and node lifecycle management
5. LLM integration prototype (`modules/ai-console/`)
