# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**RosDeck** is a self-hosted web-based control panel for ROS 2 devices on local networks. It provides system/network/storage monitoring, file and terminal access, log viewing, process and service management, and lays the foundation for "frontend–ROS–AI" collaboration.

**Tech Stack:**
- Backend: FastAPI + Uvicorn (Python 3.11), leveraging psutil, systemd, PAM, journalctl, smartctl
- Frontend: Nginx-hosted static HTML/CSS/JS with modular loading (jQuery style) + Chart.js + Toastr + Bootstrap
- Privileged operations: setuid C helpers for PAM authentication and power control; Python services execute high-risk operations via command-line tools (`mount`, `mkfs.*`, `systemctl`, etc.)

**Key Features:**
- System monitoring (CPU, memory, disk, network, uptime)
- Storage management (disk/partition info, cleanup, mount/unmount, format, SMART diagnostics)
- Runtime center (process list, systemd service management)
- Network management (interface monitoring, traffic history, connection stats)
- Logs (journalctl integration with filtering)
- Web terminal (xterm.js-based)
- File transfer (upload/download with admin/user scope)
- ROS monitoring (placeholder for nodes/topics/services)

## Development Commands

### Full Stack Development (requires sudo)
```bash
scripts/run_dev.sh
```
- Compiles and installs privileged helpers to `/usr/local/libexec/`
- Syncs frontend to `/usr/share/nginx/html/rosdeck`
- Installs Nginx configuration
- Creates virtual environment and installs dependencies
- Starts backend at `127.0.0.1:4162`
- Performs health check

### Backend Only (no sudo required)
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 4162
```

For frontend development without Nginx:
```bash
cd html && python -m http.server 8000
```

### Stop Services
```bash
scripts/stop.sh
```
Cleans up Uvicorn processes by PID/port.

### Health Check
```bash
curl -fsS http://127.0.0.1:4162/api/health
```

### View Logs
```bash
tail -f /var/log/rosdeck/backend.log
```

## Architecture Overview

### Backend Structure

**Entry Point:** `backend/app/main.py`
- Configures CORS (allows `http://localhost:1221`)
- Registers request logging middleware
- Mounts all business routes
- Provides health check at `/api/health` and CSRF token at `/api/csrf-token`

**Routes** (`backend/app/routes/`):
- `auth.py`: PAM-based login, admin mode verification, logout; includes in-memory rate limiting
- `system.py`: System status, power control (reboot/shutdown) - requires admin + CSRF
- `device.py`: Device info (hostname, architecture, distribution)
- `ros.py`: ROS metrics (nodes/topics/services count) - returns defaults when ROS environment unavailable
- `files.py`: Directory browsing, upload (50MB limit), download, delete - users restricted to `$HOME`, admins have global access
- `terminal.py`: WebSocket-based interactive shell with command blacklist and timeout
- `logs.py`: journalctl queries (priority/keyword/time range/cursor) with admin-relaxed limits
- `network.py`: Network interface monitoring, traffic history, connection stats, admin network configuration
- `storage.py`: Disk overview, partition list, report export (JSON/CSV), admin operations (cleanup/mount/partition/SMART) with operation logging
- `runtime.py`: Process list (with sorting/filtering), systemd service status; admin can terminate processes and control services

**Services** (`backend/app/services/`):
- `system_monitor.py`: psutil sampling with 2-second cache
- `network_monitor.py`: Interface traffic with 180-point historical data
- `journalctl_reader.py`: Wraps journalctl commands with permission hints
- `storage_monitor.py`: Maintains 10-minute sliding window (5s interval) for disk metrics and IO rates
- `storage_operations.py`: Provides whitelist cleanup, `mount/umount`, `mkfs/wipefs`, `smartctl` operations; full logging with `log_id` tracking
- `process_monitor.py`: Two-phase CPU/memory sampling with sorting and safe termination
- `service_manager.py`: Calls `systemctl` for service list, status, and start/stop/restart/enable/disable operations (requires passwordless sudo for backend user)
- `terminal_manager.py`: PTY-based terminal session management
- `ros_monitor.py`: ROS environment detection and metrics collection

**Dependencies** (`backend/app/deps/`):
- `csrf.py`: In-memory CSRF token generation and validation
- `admin_auth.py`: In-memory admin session management (30-minute timeout)
- `rate_limit.py`: In-memory login rate limiting

**Security Notes:**
- Session and CSRF tokens are in-memory only - lost on service restart
- Multi-instance deployment requires external storage (Redis/SQLite)
- Storage operations assume backend runs with sufficient privileges (root or equivalent)
- Runtime module requires passwordless sudo configuration for `systemctl` operations

### Frontend Structure

**Entry Point:** `html/index.html` + `html/index.js`
- Modular sidebar navigation with dynamic content loading
- Top status ribbon polls `/api/system/status` for real-time metrics
- Admin mode button triggers `/api/auth/verify-admin`, broadcasts `rosdeck:admin-mode-change` event
- CSRF token management via `sessionStorage`
- Toastr for notifications

**Module Loading Pattern:**
- Modules loaded from `modules/{modulePath}/index.html`
- Each module can have `style.css` and `main.js`
- Module scripts can define `window.moduleInit()` for initialization and `window.moduleCleanup()` for teardown

**Key Modules:**
- `modules/overview/`: System and ROS overview cards with polling
- `modules/file-transfer/`: File tree, upload, download, delete; admin mode switches directory scope
- `modules/terminal/`: xterm.js-based web terminal with themes, history, command blacklist
- `modules/logs/`: Graphical log filter + dark theme display; supports load more/refresh/permission hints
- `modules/network/`: Interface list, traffic charts, connection details, IP configuration, diagnostics
- `modules/storage/`: Disk donut charts, history trends, report export, admin operation panel (with countdown confirmation, operation logs, SMART reports)
- `modules/runtime/`: Process/Service tabs; supports search, sorting, auto-refresh, admin operation buttons with dynamic visibility

**Common Dependencies:**
- jQuery, Bootstrap 5, Toastr, Chart.js (global)
- xterm.js (loaded on-demand by terminal module)
- Styles: `html/index.css` + per-module `style.css`

### Privileged Helpers

**Location:** `privileged/`

**C Helpers:**
- `rosdeck_auth_helper.c`: PAM authentication for specified user (default root), requires setuid installation
- `src/rosdeck_control_helper.c`: Power control via systemctl (reboot/shutdown), setuid executable

**Installation:**
- Compiled and installed by `scripts/run_dev.sh` to `/usr/local/libexec/`
- Requires sudo for compilation and setuid bit setting

**Python Service Dependencies:**
- System commands: `mount`, `umount`, `mkfs.*`, `wipefs`
- Diagnostics: `smartctl` (for SMART functionality)
- System control: `systemctl`, `journalctl`, `ip`, `ping`
- Ensure backend user has appropriate privileges, configure sudoers as needed

## Important Code Patterns

### Admin-Only Operations

Admin operations require three components:
1. Admin session cookie (`admin_session_id`)
2. CSRF token validation
3. Dependency injection in route

Example:
```python
@router.post(
    "/storage/cleanup",
    dependencies=[
        Depends(csrf_protection.validate_token),
        Depends(admin_auth.require_admin),
    ],
)
async def cleanup_storage(
    body: CleanupRequest,
    username: str = Depends(get_current_username),
):
    # Operation implementation
```

### Frontend Admin Mode Detection

Modules listen for admin mode changes:
```javascript
window.addEventListener('rosdeck:admin-mode-change', (e) => {
    const isAdmin = e.detail.active;
    // Show/hide admin controls
});
```

### Module Lifecycle

Each module script should define:
```javascript
window.moduleInit = function() {
    // Initialize module
    // Start polling/event listeners
};

window.moduleCleanup = function() {
    // Clean up intervals/event listeners
    // Prevent memory leaks
};
```

### Service Layer Caching

Services like `storage_monitor` and `system_monitor` implement sampling intervals to avoid excessive system calls:
```python
class StorageMonitor:
    SAMPLE_INTERVAL_SECONDS = 5  # Minimum sampling interval

    def _ensure_sample(self):
        if (current_time - self._last_sample_time) < self.SAMPLE_INTERVAL_SECONDS:
            return self._last_summary  # Return cached data
        return self._collect_sample_locked(current_time)
```

### Operation Logging Pattern

High-risk operations use `storage_operations` pattern:
- Log operation start with `log_id`
- Execute operation with comprehensive error handling
- Return `log_id` in response for audit trail
- Store logs in memory (max 100 entries)

Example:
```python
log_id = self._log_operation(
    action="cleanup",
    params={"target": target, "dry_run": dry_run},
    actor=actor,
    status="started",
)
# Execute operation
self._update_log(log_id, status="completed", result=result)
return {"success": True, "log_id": log_id, **result}
```

## Known Limitations

1. **Session/CSRF Security**: Sessions and CSRF tokens are in-memory only; lost on service restart; no distributed deployment support
2. **High-Risk Commands**: Storage operations have whitelists and countdown timers but still require manual confirmation; consider command auditing and fine-grained permissions
3. **Sudo Configuration**: `systemctl`, `mount` operations depend on environment sudoers configuration; must be documented in deployment guide
4. **Operation Logging**: Operation logs are memory-only (max 100 entries); no persistence or alerting
5. **Testing**: No pytest for backend, no unit/e2e tests for frontend; critical modules need test coverage
6. **Documentation**: `docs/ARCHITECTURE.md` is placeholder; deployment/security policies need formal documentation
7. **Internationalization**: All UI and messages are in Chinese; internationalization requires additional effort

## Current Development Status

Recent commits show focus on:
- Layout adjustments for better readability (commits fc5b7bb, b1129fd)
- File transfer and runtime center implementation (commit ed3ec68)
- Logs and network management modules (commit 0729d7e)
- Terminal functionality with pending font color and admin permission issues (commit 766a562)

See `AGENTS.md` for detailed module status and implementation notes.
See `TODO.md` for planned ROS module features.

## Common Troubleshooting

**Backend won't start:**
- Check port 4162 availability: `ss -ltnp | grep 4162`
- Check backend log: `tail -f /var/log/rosdeck/backend.log`
- Verify virtual environment: `source backend/.venv/bin/activate`

**File/Terminal operations fail:**
- Verify session cookie `session_id` exists
- Admin operations require `admin_session_id` cookie

**Log module permission errors:**
- Add backend user to `systemd-journal` group
- Or configure sudo wrapper for journalctl

**Network module static IP fails:**
- Verify `netplan` and `ip` command availability
- Ensure admin mode is active
- Check Chart.js resource loading

**Storage module issues:**
- SMART requires `smartctl` installation and device access permissions
- Mount/format failures: check backend user privileges and target device status
- Report export is read-only HTTP; ensure browser allows downloads

**Runtime center issues:**
- Process termination/service control require admin mode + CSRF
- `sudo systemctl` prompts for password: adjust `/etc/sudoers` for passwordless execution
