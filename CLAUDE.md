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
- `terminal.py` - WebSocket endpoint for terminal sessions with PTY support

**Services** (`services/`):
- `system_monitor.py` - psutil wrapper with 2-second cache
- `ros_monitor.py` - Background thread running rclpy node with 5s refresh; falls back to defaults if rclpy unavailable
- `admin_privileged.py` - Subprocess wrapper for `/usr/local/libexec/rosdeck-control-helper`
- `terminal_manager.py` - PTY session manager with command blacklist and admin-only command filtering

**Dependencies** (`deps/`):
- `csrf.py` - In-memory CSRF token storage (needs Redis/DB for multi-instance)
- `admin_auth.py` - In-memory admin session management (30min TTL), session format: `session_{username}_{timestamp}`
- `rate_limit.py` - IP-based rate limiting (5 attempts/5min)

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
- `overview` - Dashboard with system and ROS metrics polling
- `file-transfer` - COMPLETE: Directory listing with breadcrumbs, upload (50MB limit), download, delete; normal users restricted to `~`, admins have full access with warnings for sensitive paths (`/etc`, `/bin`, `/sbin`, `/usr`, `/boot`, `/var`)
- `terminal` - COMPLETE: Full xterm.js integration with WebSocket PTY, command blacklist (rm -rf /, mkfs, dd, fork bomb), admin-only commands (sudo, systemctl), 4 themes (dark/light/dracula/monokai), command history tracking, 30min session timeout
- `network` - IN PROGRESS: Network interface monitoring and management (see Network Module Implementation Plan below)
- `storage` - Partial: Has realtime panel, chart, and summary view components
- `logs` - Stub: Basic structure only (parallel development with Network module)
- `runtime` - Stub: Basic structure only
- `ros/*` - Partial: ROS-specific pages (overview, communication, operations, ai-commander) with shared utilities

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
- `ROSMonitor` runs `rclpy.spin()` in daemon thread with SingleThreadedExecutor
- Collects node/topic/service counts every 5s with stability calculation
- Gracefully degrades if rclpy import fails (logs warning, returns default values with ROS version from `$ROS_DISTRO`)
- Thread-safe stats access via lock and primed event pattern

### Terminal Architecture
- WebSocket endpoint at `/api/terminal/ws` validates session_id cookie before upgrade
- Each session spawns PTY via ptyprocess with user's shell and environment
- Command filtering occurs before execution via `command_check` message type
- Blacklisted patterns (destructive commands) rejected for all users
- Admin-only patterns (sudo, systemctl) require valid admin_session_id cookie
- Frontend uses xterm.js with fit addon, theme persistence via localStorage
- Input buffering up to 4KB when disconnected with overflow warning

### Network Module Implementation Plan

**Feature Scope** (based on requirements):
1. **Interface Monitoring** (all users):
   - List all network interfaces with basic info (name, IP, MAC, status) + traffic stats (TX/RX bytes, errors, drops)
   - Real-time traffic graph: Upload/Download separate lines, 5s refresh aligned with system status polling
   - Time window switcher: 1min (12 points) / 5min (60 points) / 15min (180 points)

2. **Connection Details** (all users with graceful degradation):
   - Detailed connection table: Local addr:port ↔ Remote addr:port, State (ESTABLISHED/TIME_WAIT/etc), PID/Program
   - Fallback: If lacking permissions, show aggregated counts only
   - Implementation: Prioritize `/proc/net/tcp` parsing over psutil for reliability in non-root scenarios

3. **Network Configuration** (admin only):
   - Interface control: Enable/Disable (via `ip link set {iface} up/down`)
   - Static IP configuration: IP/netmask/gateway (temporary via `ip addr`/`ip route`, persistent via netplan)
   - Mode toggle: Temporary (runtime only) vs Persistent (write to `/etc/netplan/*.yaml`)
   - Network diagnostics: Ping (frontend form to target host), shows live output

4. **Security**:
   - Dangerous operations require confirmation dialog: Disabling all active interfaces, changing IP of current management interface
   - Admin session validation for all configuration endpoints
   - CSRF protection on all POST/PUT/DELETE operations

**Architecture**:

**Backend** (`backend/app/`):
```
routes/network.py           [NEW] - Network management API
  GET  /api/network/interfaces        - List interfaces with stats
  GET  /api/network/traffic-history   - Historical data for charts (cached in-memory)
  GET  /api/network/connections       - Active TCP/UDP connections
  POST /api/network/interface/toggle  - Enable/disable interface (admin + CSRF)
  POST /api/network/interface/config  - Set static IP (admin + CSRF)
  POST /api/network/diagnostic/ping   - Run ping test (admin + CSRF)

services/network_monitor.py [NEW] - Network data collection service
  class NetworkMonitor:
    - get_interfaces() -> List[InterfaceInfo]  # Uses psutil.net_if_addrs/stats
    - get_traffic_history(window: str) -> Dict  # Rolling buffer, 5s sampling
    - get_connections() -> List[ConnectionInfo]  # Parses /proc/net/{tcp,udp}
    - execute_interface_toggle(iface: str, enable: bool) -> bool
    - execute_ip_config(iface: str, ip: str, netmask: str, gateway: str, persistent: bool) -> bool
    - execute_ping(target: str) -> str  # Runs ping -c 4, returns output
```

**Frontend** (`html/modules/network/`):
```
index.html    [NEW] - Three-column layout:
  <div class="network-container">
    <aside class="interfaces-panel">        <!-- Left: Interface list -->
    <main class="traffic-panel">            <!-- Center: Real-time chart -->
    <aside class="connections-panel">       <!-- Right: Connection table -->
    <section class="config-panel">          <!-- Admin-only config form (collapsible) -->
  </div>

main.js       [NEW] - Module logic (~800 lines estimated)
  - Polling: 5s interval aligned with system status (reuses existing timer)
  - Chart: Chart.js line chart, two datasets (upload/download), time-sliding window
  - Interface actions: Click interface → highlight in chart, admin toggle button
  - Config form: IP/netmask validation, mode toggle (temp/persistent), submit with CSRF
  - Event listeners: Admin mode change → show/hide config panel

style.css     [NEW] - Responsive three-column grid, interface card styling
```

**No New Privileged Helpers**: All network operations use `subprocess.run()` with `sudo` (requires admin session validation). Commands whitelist:
- `ip link set {iface} up/down`
- `ip addr add/del`
- `ip route add/del`
- `netplan apply` (after file write)
- `ping -c 4 {target}`

**Data Flow**:
1. **Traffic Graph**:
   - Backend: `NetworkMonitor` maintains circular buffer (max 180 points = 15min @5s)
   - Frontend polls `/api/network/traffic-history?window=5min` every 5s
   - Chart.js updates with new data point, shifts old data

2. **Interface Toggle**:
   - User clicks "Disable eth0" → Confirmation dialog (warns if management interface)
   - POST `/api/network/interface/toggle` with CSRF token
   - Backend validates admin session, runs `sudo ip link set eth0 down`
   - Frontend refreshes interface list

3. **Static IP Config**:
   - Admin fills form: eth0, 192.168.1.100/24, gateway 192.168.1.1, mode: Persistent
   - POST `/api/network/interface/config`
   - Backend:
     - Runs `ip addr add 192.168.1.100/24 dev eth0`
     - Writes to `/etc/netplan/99-rosdeck-eth0.yaml`
     - Runs `sudo netplan apply`
   - Shows success toast with warning about connection loss if changing current interface

**Modified Files**:
```
backend/app/routes/network.py               [CREATE]
backend/app/services/network_monitor.py     [CREATE]
backend/app/main.py                         [UPDATE] - Add network router
html/modules/network/index.html             [CREATE]
html/modules/network/main.js                [CREATE]
html/modules/network/style.css              [CREATE]
CLAUDE.md                                   [UPDATE] - This section
```

**No modifications to**:
- `system_monitor.py` - Network module uses dedicated `network_monitor.py`
- Nginx config - Uses standard HTTP polling, no WebSocket needed
- Privileged helpers - Uses subprocess with sudo commands

## Important Constraints

### Security
- Never commit production configs or secrets
- Audit privileged helper changes carefully (they run as root with setuid 4755)
- Both helpers sanitize environment variables (LD_PRELOAD, LD_LIBRARY_PATH, PYTHONPATH) before exec
- CSRF tokens and admin sessions are in-memory (won't survive restarts)
- Consider Redis/DB for production multi-instance deployments
- Terminal command blacklist uses regex patterns to prevent destructive operations
- File operations validate paths to prevent directory traversal attacks

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
- Kill old process: `cat /run/rosdeck-backend.pid | xargs kill`

**Frontend loads but APIs fail:**
- Verify Nginx proxy config at `/etc/nginx/conf.d/rosdeck.conf`
- Check CSRF token in browser console (should be in sessionStorage)
- Confirm backend health: `curl http://127.0.0.1:4162/api/health`
- Ensure session_id cookie is present (Nginx blocks API calls without it)

**Privileged operations fail:**
- Ensure helpers compiled and setuid: `ls -la /usr/local/libexec/rosdeck-*`
- Both should show `-rwsr-xr-x root root` (4755 permissions)
- Check PAM configuration on the system (requires libpam-dev for compilation)
- Review backend logs for subprocess errors

**Terminal issues:**
- WebSocket connection rejected: Check session_id cookie validity
- Command blocked: Check `terminal_manager.py` blacklist/admin patterns
- PTY spawn fails: Verify ptyprocess dependency and user's shell path
- Font rendering issues: Ensure xterm.js assets loaded from `libs/xterm/`

**Network module issues:**
- Interface list empty: Check if user has permission to read `/sys/class/net/`
- Connection details unavailable: Normal for non-root users, should show aggregate counts
- IP config fails: Verify netplan is installed and user is in admin mode
- Traffic graph not updating: Check if 5s polling interval is active in browser console

**ROS metrics show zeros:**
- Verify `rclpy` is importable in backend venv: `python -c "import rclpy"`
- Check `ROS_DISTRO` and source ROS setup scripts before starting backend
- Look for ROSMonitor warnings in backend logs
- ROSMonitor gracefully degrades - zeros indicate rclpy unavailable or no nodes running

## Dependencies

**Backend** (`backend/requirements.txt`):
- FastAPI 0.104.1 + Uvicorn 0.24.0 (ASGI server)
- WebSockets 12.0 (for terminal)
- psutil 5.9.6 (system monitoring)
- python-pam 2.0.2 (authentication)
- python-multipart 0.0.20 (file uploads)
- ptyprocess 0.7.0 (terminal PTY)
- pydantic 1.10.13 (data validation)

**Frontend** (`html/libs/`):
- jQuery 3.x (DOM manipulation)
- Bootstrap 5.x (UI framework)
- Chart.js (data visualization)
- Toastr (notifications)
- xterm.js (terminal emulator)

**System**:
- Nginx (reverse proxy and static file serving)
- GCC + libpam-dev (for compiling privileged helpers)
- Python 3.11+ (backend runtime)
- Optional: ROS 2 with rclpy (for ROS monitoring)

## Future Directions

See `AGENTS.md` section 13 for roadmap. Key themes:
1. Complete system monitoring modules (logs, network realtime bandwidth, storage detailed views)
2. Network management (Wi-Fi/Ethernet switching, firewall configuration)
3. ROS graph visualization and node lifecycle management
4. LLM integration prototype for ROS interaction (`modules/ros/ai-commander`)
5. Automated testing (pytest for backend, manual checklists for frontend)
6. Production deployment guide (systemd service, Redis for session storage, SSL/TLS)
