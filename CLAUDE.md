# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RosDeck is an open-source LAN console for monitoring and managing development boards or edge devices running ROS 2. It provides lightweight server management capabilities (disk/network monitoring, file transfer, terminal, logs) with plans to integrate LLM-based interactions with ROS resources.

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
- `logs.py` - System logs streaming via journalctl, keyword filtering, export functionality
- `network.py` - Network interface monitoring, traffic stats, connection details, IP configuration, diagnostics
- `runtime.py` - Process and systemd service management with admin-only control operations

**Services** (`services/`):
- `system_monitor.py` - psutil wrapper with 2-second cache
- `ros_monitor.py` - Background thread running rclpy node with 5s refresh; falls back to defaults if rclpy unavailable
- `admin_privileged.py` - Subprocess wrapper for `/usr/local/libexec/rosdeck-control-helper`
- `terminal_manager.py` - PTY session manager with command blacklist and admin-only command filtering
- `journalctl_reader.py` - Systemd journal reader with streaming support and filtering
- `network_monitor.py` - Network interface stats collection, traffic history buffer (180 points), connection parsing
- `process_monitor.py` - Process listing via psutil with sorting, filtering, and kill operations
- `service_manager.py` - Systemd service management (list, start, stop, restart, enable, disable)

**Dependencies** (`deps/`):
- `csrf.py` - In-memory CSRF token storage (needs Redis/DB for multi-instance)
- `admin_auth.py` - In-memory admin session management (30min TTL), session format: `session_{username}_{timestamp}`
- `rate_limit.py` - IP-based rate limiting (5 attempts/5min)

**Entry Point**: `main.py` (port 4162)
- CORS configured for `http://localhost:1221`
- Logs all requests via middleware
- Serves login page with injected CSRF token at `/auth/login.html`
- Registers all route modules (auth, system, device, ros, files, terminal, logs, network, storage, runtime)

### Frontend Structure (`html/`)

**Global Dependencies** (`html/index.html`):
- jQuery 3.x, Bootstrap 5.x, Toastr loaded globally
- **Chart.js** loaded globally for all modules requiring data visualization
- xterm.js loaded on-demand by terminal module

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

**Completed Modules**:

- **`overview`** - COMPLETE: Dashboard with system and ROS metrics polling

- **`file-transfer`** - COMPLETE: Directory listing with breadcrumbs, upload (50MB limit), download, delete; normal users restricted to `~`, admins have full access with warnings for sensitive paths (`/etc`, `/bin`, `/sbin`, `/usr`, `/boot`, `/var`)

- **`terminal`** - COMPLETE: Full xterm.js integration with WebSocket PTY, command blacklist (rm -rf /, mkfs, dd, fork bomb), admin-only commands (sudo, systemctl), 4 themes (dark/light/dracula/monokai), command history tracking, 30min session timeout

- **`logs`** - COMPLETE: Real-time journalctl log streaming via WebSocket, keyword filtering with highlighting, priority level filter (ERROR/WARNING/INFO/DEBUG), line limit selector (100/500/1000), auto-scroll toggle, export to text file, graceful error handling

- **`network`** - COMPLETE: Three-column layout with:
  - **Left panel**: Interface list (name, IP, MAC, status, TX/RX stats, errors, drops); admin can enable/disable interfaces
  - **Center panel**: Real-time traffic chart (Chart.js) with upload/download speeds, time window selector (1/5/15 min), speed display
  - **Right panel**: Active connections table (TCP/UDP, local/remote address:port, state, process); falls back to aggregate counts if permissions insufficient
  - **Admin config panel** (collapsible): Static IP configuration (temporary/persistent via netplan), ping diagnostic tool
  - Traffic history cached in-memory (5s intervals, max 180 points)

- **`runtime`** - COMPLETE: Tab-based layout for process and service management:
  - **Process Management Tab**: List all running processes with PID, name, user, CPU%, memory%, status, command line; sortable by CPU/memory/PID/name; searchable; 5-second auto-refresh; admin can kill processes
  - **Service Management Tab**: List systemd services with name, load state, active state, sub-state; searchable and filterable by status; admin can start/stop/restart services and enable/disable autostart
  - Admin mode detection via global `window.adminModeActive` variable and `rosdeck:admin-mode-change` event
  - Performance optimized: removed per-service enabled check for faster loading

**Partial/Stub Modules**:
- `storage` - Partial: Has realtime panel, chart, and summary view components
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

### Logs Architecture
- **Backend**: `JournalctlReader` service wraps `journalctl` subprocess
  - Streaming mode: `journalctl -f` with line-by-line parsing
  - Query mode: Time-limited queries with `--since`, `--lines` parameters
  - Priority filtering via `-p` flag (emerg=0, alert=1, crit=2, err=3, warning=4, notice=5, info=6, debug=7)
  - Output format: JSON (`-o json`) for structured parsing
- **API Routes**:
  - `GET /api/logs/stream` - WebSocket endpoint for real-time log streaming
  - `GET /api/logs/query` - Query historical logs with filters
  - `GET /api/logs/export` - Download logs as text file
- **Frontend**:
  - WebSocket connection established in `moduleInit()`
  - Keyword filtering implemented client-side with regex highlighting
  - Auto-scroll with manual pause/resume capability
  - Virtual scrolling for performance (displays last N lines)
  - Export triggers server-side journalctl dump with same filters

### Network Architecture
- **Backend**: `NetworkMonitor` service with traffic history buffer
  - Uses `psutil.net_if_addrs()` and `psutil.net_if_stats()` for interface data
  - Maintains circular buffer (180 data points, 5s intervals = 15min max window)
  - Calculates upload/download rates via delta between samples
  - Connection parsing: Attempts detailed list via `psutil.net_connections()`, falls back to counts if PermissionError
  - Admin operations via `subprocess.run()` with sudo: `ip link`, `ip addr`, `ip route`, `netplan apply`, `ping`
- **API Routes**:
  - `GET /api/network/interfaces` - List all interfaces with stats
  - `GET /api/network/traffic-history?window={1min|5min|15min}` - Historical traffic data
  - `GET /api/network/connections` - Active TCP/UDP connections (or aggregate counts)
  - `POST /api/network/interface/toggle` - Enable/disable interface (admin, CSRF)
  - `POST /api/network/interface/config` - Configure static IP (admin, CSRF, supports temporary/persistent modes)
  - `POST /api/network/diagnostic/ping` - Run ping test (admin, CSRF)
- **Frontend**:
  - Three-column grid layout (`grid-template-columns: minmax(340px, 1fr) minmax(600px, 2fr) minmax(400px, 1.2fr)`)
  - Traffic chart auto-sized to fill available height (`height: calc(100vh - 160px - 100px)`)
  - Chart.js line chart with 2 datasets (upload red, download blue)
  - Polling interval aligned with global system status update (5s)
  - Admin panel shows/hides based on `rosdeck:admin-mode-change` event
  - Dangerous operations (disable interface, change IP) require confirmation dialogs

### Runtime Architecture
- **Backend**: Process and service management via psutil and systemctl
  - `ProcessMonitor`: Uses `psutil.process_iter()` with two-pass approach for accurate CPU% calculation
  - `ServiceManager`: Parses `systemctl list-units` output, optimized by removing per-service enabled check
  - Process kill operations check for PID 1 (init) and self-termination prevention
  - All service control operations (start/stop/restart/enable/disable) use `sudo systemctl`
- **API Routes**:
  - `GET /api/runtime/processes?sort_by={cpu|memory|pid|name}` - List processes (all users, read-only)
  - `POST /api/runtime/processes/kill` - Terminate process by PID (admin + CSRF)
  - `GET /api/runtime/services` - List systemd services (all users, read-only)
  - `POST /api/runtime/services/action` - Control service (admin + CSRF, actions: start/stop/restart/enable/disable)
  - `GET /api/runtime/services/{name}/status` - Get detailed service status (all users)
- **Frontend**:
  - Bootstrap 5 Tab layout with two panels (Processes / Services)
  - Process table: sortable, searchable, 5-second auto-refresh, admin-only kill button per process
  - Service table: searchable, filterable by active state, admin-only action buttons (5 buttons per service)
  - Admin mode detection via global `window.adminModeActive` variable, synced via `rosdeck:admin-mode-change` event on window
  - Module stores local `isAdminMode` state initialized from global on `moduleInit()`, updated via event handler
  - **CRITICAL CSS FIX**: `.tab-pane` must not have `display: flex` without `.show` class to allow Bootstrap tab switching

## Important Constraints

### Security
- Never commit production configs or secrets
- Audit privileged helper changes carefully (they run as root with setuid 4755)
- Both helpers sanitize environment variables (LD_PRELOAD, LD_LIBRARY_PATH, PYTHONPATH) before exec
- CSRF tokens and admin sessions are in-memory (won't survive restarts)
- Consider Redis/DB for production multi-instance deployments
- Terminal command blacklist uses regex patterns to prevent destructive operations
- File operations validate paths to prevent directory traversal attacks
- Network operations validate interface names and IP formats before execution
- Logs module uses read-only journalctl operations (no log deletion/modification)

### Frontend Conventions
- 4-space indentation, camelCase functions
- Double quotes for strings
- No emojis in UI unless explicitly requested
- Module names map to paths: `'file-transfer'` → `modules/file-transfer/`
- All modules must implement `moduleInit()` and `moduleCleanup()`
- Use Chart.js for data visualization (already loaded globally in `index.html`)

### Backend Conventions
- Follow PEP 8 / Black style
- Use type hints where helpful
- Log at appropriate levels (INFO for normal ops, WARNING for degraded state, ERROR for failures)
- Routes return `{"success": bool, "message": str, ...}` for consistency
- Use `subprocess.run()` with `capture_output=True, text=True, timeout=30` for external commands
- Always validate user input before passing to shell commands

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

**Logs module issues:**
- WebSocket connection fails: Check if journalctl is available and accessible
- No logs displayed: Verify systemd journal exists at `/var/log/journal/` or `/run/log/journal/`
- Permission errors: Normal users can only read their own logs; system logs require admin mode
- Export fails: Check backend has write permissions to temporary directory

**Network module issues:**
- Interface list empty: Check if user has permission to read network interface data via psutil
- Connection details show "Permission denied": Normal for non-root users, aggregate counts are displayed instead
- Traffic chart not updating: Verify 5s polling interval is active in browser console
- IP config fails: Ensure netplan is installed (`apt install netplan.io`) and admin mode is active
- Interface toggle fails: Verify admin session and check for existing network manager conflicts (NetworkManager, systemd-networkd)
- Chart.js error: Ensure Chart.js is loaded in main `index.html` before module scripts

**ROS metrics show zeros:**
- Verify `rclpy` is importable in backend venv: `python -c "import rclpy"`
- Check `ROS_DISTRO` and source ROS setup scripts before starting backend
- Look for ROSMonitor warnings in backend logs
- ROSMonitor gracefully degrades - zeros indicate rclpy unavailable or no nodes running

**Runtime module issues:**
- Admin buttons not appearing: Check browser console for admin mode initialization logs
- Verify `window.adminModeActive` is defined in global scope (set by index.js)
- Ensure `rosdeck:admin-mode-change` event is being dispatched on window object
- Check that module is listening to window events, not document events
- Service loading slow: Check if per-service enabled check was re-added (should be disabled for performance)
- Processes not refreshing: Verify 5s interval is active and tab has `.active` class

## Dependencies

**Backend** (`backend/requirements.txt`):
- FastAPI 0.104.1 + Uvicorn 0.24.0 (ASGI server)
- WebSockets 12.0 (for terminal and logs streaming)
- psutil 5.9.6 (system monitoring, network stats)
- python-pam 2.0.2 (authentication)
- python-multipart 0.0.20 (file uploads)
- ptyprocess 0.7.0 (terminal PTY)
- pydantic 1.10.13 (data validation)

**Frontend** (`html/libs/`):
- jQuery 3.x (DOM manipulation)
- Bootstrap 5.x (UI framework)
- Chart.js 4.x (data visualization, loaded globally)
- Toastr (notifications)
- xterm.js (terminal emulator)

**System**:
- Nginx (reverse proxy and static file serving)
- GCC + libpam-dev (for compiling privileged helpers)
- Python 3.11+ (backend runtime)
- systemd with journalctl (for logs module)
- iproute2 (ip command for network configuration)
- netplan (optional, for persistent network config)
- iputils-ping (for network diagnostics)
- Optional: ROS 2 with rclpy (for ROS monitoring)

## Future Directions

See `AGENTS.md` section 13 for roadmap. Key themes:
1. Complete storage module (detailed disk usage breakdown, SMART status, cleanup utilities)
2. Enhanced network management (Wi-Fi configuration, firewall rules via ufw/iptables)
3. ROS graph visualization and node lifecycle management
4. LLM integration prototype for ROS interaction (`modules/ros/ai-commander`)
5. Automated testing (pytest for backend, manual checklists for frontend)
6. Production deployment guide (systemd service, Redis for session storage, SSL/TLS)
7. Log analysis features (pattern detection, error aggregation, export to standard formats)
8. Network traffic analysis (bandwidth usage per process, connection history)

## Recent Changes (v0.4.0)

**Added:**
- Runtime module (process and service management) with dual-tab interface
  - Process monitoring: view all processes, sort by CPU/memory/PID/name, search, auto-refresh every 5s
  - Service management: list systemd services, filter by status, admin can control services
  - Admin operations: kill processes, start/stop/restart services, enable/disable service autostart
  - Performance optimization: removed slow per-service enabled check, reduced load time from 10s+ to <2s
- Backend services: `process_monitor.py`, `service_manager.py`
- Backend route: `/api/runtime/*` with admin + CSRF protection

**Fixed:**
- Runtime module Tab switching: CSS `.tab-pane` forced `display: flex` breaking Bootstrap tab visibility
- JavaScript template literal escaping in main.js (was `\`` instead of backticks)
- Service table column count (removed "enabled" column, now 5 columns instead of 6)
- Admin mode detection in runtime module: Changed from cookie reading (which returned empty string) to using global `window.adminModeActive` variable pattern like other modules

**Known Limitations:**
- CSRF tokens and admin sessions stored in-memory (lost on restart)
- Network traffic history limited to 15 minutes (180 data points)
- No log persistence/archiving (relies on systemd journal retention)
- Static IP configuration requires netplan (Ubuntu/Debian specific)
- Connection details require elevated permissions (falls back to counts)
