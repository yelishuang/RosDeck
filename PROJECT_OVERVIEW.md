# RosDeck Project Overview

## Purpose & Architecture
- **Goal**: Provide an on-device control deck for ROS 2 development boards, combining basic fleet admin (login, device telemetry, power control) with ROS runtime visibility through a LAN-accessible UI.
- **Architecture**: A FastAPI backend (`backend/app`) exposed through Nginx (listening on `:1221`) proxies, plus a static HTML/CSS/JS frontend (`html/`) served from the same Nginx site. Authentication relies on Linux PAM with CSRF + rate limiting safeguards. Frontend modules poll REST APIs for system/ROS telemetry.
- **Runtime topology**: Users reach Nginx → Nginx injects CSRF and proxies `/api/*` to Uvicorn (`:4162`). Frontend assets are delivered as static files; dynamic data flows through the FastAPI JSON APIs. Admin-level actions (power control) are gated on a separate in-memory session managed in the backend.

## Repository Map
- `backend/` – FastAPI service.
  - `app/main.py` – App factory, FastAPI middleware (CORS, logging), route registration, health/metrics endpoints, login page renderer with CSRF token.
  - `app/routes/` – REST endpoints for auth, system telemetry, device info, ROS stats.
  - `app/services/` – psutil- and ROS CLI-backed telemetry helpers (`system_monitor`, `ros_monitor`).
  - `app/deps/` – Dependency injectables (CSRF manager, admin session manager, login rate limiter).
  - `config.example.yaml` – Placeholder config structure; no live settings yet.
  - `requirements.txt` – Python dependencies (FastAPI stack, psutil, python-pam).
- `html/` – Static frontend delivered via Nginx.
  - `index.html`, `index.js`, `index.css` – Main dashboard shell with sidebar navigation, module loading, polling loops.
  - `auth/` – Login experience (`login.html/js/css`) that fetches CSRF-injected markup from the backend and posts to `/api/auth/login`.
  - `modules/` – Feature panels (overview + several stubs for logs, storage, ROS tools, terminal, etc.).
  - `libs/` – Third-party assets (Bootstrap, jQuery, Chart.js, xterm.js, Toastr).
  - `shared/` – Common stylesheet snippets.
- `nginx/rosdeck.nginx.conf` – Reverse-proxy/site config that protects routes, proxies APIs/websocket, and injects login page via backend.
- `scripts/` – Ops helpers.
  - `run_dev.sh` – Opinionated provisioning script: sync frontend → `/usr/share/nginx/html/rosdeck`, install/enable Nginx, create Python venv, run backend under Uvicorn, manage sudoers for power control.
  - `stop.sh` – PID/port-based backend shutdown helper.
  - `rosdeck-backend.service` – Placeholder systemd unit skeleton.
  - `backend.log` – Example log artifact (kept under version control).
- `docs/ARCHITECTURE.md` – Skeleton note earmarking future architecture documentation.

## Main Workflows & Data Flow
- **Login flow**
  1. User hits `/auth/login.html`; Nginx proxies to FastAPI to inject a freshly minted CSRF token into the HTML meta tag (`app.main.serve_login_page`).
  2. `html/auth/login.js` posts `{username, password}` to `/api/auth/login` with that CSRF token and obeys rate limits. Backend uses PAM to validate the Linux account, sets `session_id` cookie, and responds with redirect metadata.
  3. Successful login redirects to `/index.html`; Nginx guards all other routes with `session_id` presence.
  4. Optional admin elevation posts to `/api/auth/verify-admin`; backend verifies `root` credentials and drops an `admin_session_id` cookie backed by `AdminAuthManager`.
- **Telemetry polling**
  - Dashboard bootstrap (`index.js`) loads modules dynamically and starts `updateSystemStatus()` interval calling `/api/system/status`. Responses include uptime, disk/memory/cpu usage and network throughput from `SystemMonitor`.
  - The overview module concurrently fetches `/api/ros/stats` and `/api/device/info`. `ROSMonitor` shells out to `ros2` CLI for nodes/topics/services, infers stability and version; `get_device_info` inspects hostname, platform, `/etc/os-release`, and resolves a LAN IP via UDP socket tricks.
- **Power control**
  - Frontend `handlePowerAction` is currently stubbed (UI simulation). Backend `/api/system/power` exists and requires valid CSRF + admin session. It schedules an async task to run `sudo systemctl reboot/poweroff` after five seconds; `run_dev.sh` preps sudoers rules so the backend user can execute commands passwordlessly.
- **Metrics/Audit**
  - Frontend login scripts fire lightweight metrics to `/api/metrics`. Backend filters specific events and logs via FastAPI logger (AUDIT warnings). Logs get persisted wherever the FastAPI process writes (e.g., `scripts/run_dev.sh` directs to `/var/log/rosdeck/backend.log`).
- **WebSocket placeholder**
  - Nginx proxies `/ws/` with session checks, but backend `app/ws` package is empty, so realtime comms are not implemented yet.

## Dependencies & Tooling
- **Backend runtime**
  - Python 3.11+ (assumed from cached bytecode) with FastAPI 0.104, Uvicorn 0.24, Pydantic 1.10, psutil 5.9, python-pam 2.0.
  - System binaries: `ros2` CLI (optional but required for live ROS stats), `sudo/systemctl`, `nginx`, `rsync`, `pam` modules.
  - In-memory stores for CSRF tokens, rate limit counters, and admin sessions; no persistent state/DB.
- **Frontend tooling**
  - Plain ES5/ES6 modules using jQuery, Bootstrap, Toastr, Chart.js, xterm.js (terminal module stub), served statically.
  - CSS is handcrafted (no build chain); assets referenced directly.
- **Ops tooling**
  - Nginx acts as the public edge; configuration assumes file system locations typical of Linux distributions.
  - Scripts expect sudo privileges to modify `/etc/nginx`, `/var/log`, `/usr/share/nginx`, `/etc/sudoers.d`, `/run`.
  - `scripts/run_dev.sh` manages Python virtualenv under `backend/.venv` and logs to `/var/log/rosdeck`.

## Running, Testing, Deploying
- **Local/dev run (with sudo)**
  1. Ensure Nginx and ROS 2 CLI are installed; ROS components are optional but telemetry will read as zero without them.
  2. Execute `scripts/run_dev.sh` (requires sudo to configure Nginx, sudoers, log directories). Script handles frontend sync, Nginx reload, Python venv creation, dependency install, and Uvicorn launch on `127.0.0.1:4162`.
  3. Visit `http://localhost:1221/auth/login.html`; authenticate with a Linux user present on the host. Backend health check available at `http://127.0.0.1:4162/api/health`.
  4. Use `scripts/stop.sh` to terminate the backend Uvicorn process and clear PID files.
- **Manual run (without script)**
  - `pip install -r backend/requirements.txt`, then `uvicorn app.main:app --host 127.0.0.1 --port 4162` from `backend/`. Serve `html/` statically (e.g., simple web server) but note CSRF/meta token injection is only available via FastAPI’s `/auth/login.html`, so reverse-proxying through the backend or replicating the injection logic is recommended.
- **Testing**
  - No automated test suite. Smoke tests rely on hitting `/api/health`, `/api/system/status`, `/api/device/info`, `/api/ros/stats`. Authentication can be verified via `curl` with cookies and CSRF headers. Rate limiting and admin session logic are memory-based; restarting the process resets state.
- **Deployment considerations**
  - Target environment should mirror script assumptions (Linux with systemd & nginx). Ensure service user has necessary sudoers entries but limit scope to required commands. Consider packaging the backend as a systemd unit (placeholder exists) and codifying frontend sync in deployment pipeline.

## APIs & Contracts (FastAPI)
- `GET /api/health` → `{ "status": "ok", "csrf_enabled": true }` for basic health probes.
- `POST /api/metrics` → Accepts arbitrary JSON events; logs `login_success`, `login_failed`, `file_uploaded`, `command_executed` at WARN level for audit.
- `POST /api/auth/login` → Body `{username, password}`; requires `X-CSRF-Token` header and respects rate limiting. On success sets `session_id` cookie and returns `{"ok": true, "redirect": "../index.html"}`; failure returns `401` with structured `detail`.
- `POST /api/auth/logout` → Clears `session_id` and `admin_session_id` cookies; returns `{ "ok": true }`.
- `POST /api/auth/verify-admin` → Body `{password}` (root password). Requires CSRF + rate limit; issues `admin_session_id` cookie on success with expiry metadata.
- `GET /api/system/status` → Telemetry: `{ uptime_seconds, disk:{usage_percent,...}, memory:{...}, cpu:{...}, network:{speed_mbps,...} }` sourced via `psutil`.
- `POST /api/system/power` → Body `{action: "restart"|"shutdown"}`. Requires CSRF + admin session. Schedules a delayed `systemctl` command and responds with `{ success, message, scheduled_time }`.
- `GET /api/device/info` → `{ hostname, status:"online", os, architecture, ip_address }`, deriving OS string from `/etc/os-release` when available.
- `GET /api/ros/stats` → `{ active_nodes, topics_count, services_count, stability_percent, ros_version, last_updated }`. Returns zeros if ROS 2 CLI unavailable.
- `GET /auth/login.html` → Served by FastAPI to inject CSRF meta tag before delivering static HTML (all other static assets served by Nginx).
- WebSocket endpoints are reserved under `/ws/` but not yet implemented server-side.

## Top Risks & Next Actions
- **In-memory security state**: CSRF tokens, sessions, rate limiting, and admin elevation data reset on process restart and do not scale across workers.  
  _Next step_: 1) Introduce durable storage (Redis/DB) or align deployment to single-process mode with watchdog; 2) Add session signing/verification to prevent forgery.
- **Privileged command execution**: `systemctl` reboot/poweroff is triggered via asyncio task without additional validation and relies on sudoers wildcards.  
  _Next step_: 1) Narrow sudoers scope to a dedicated service user; 2) Add backend confirmation/logging and frontend confirmation UX before enqueuing power events.
- **ROS CLI shell-outs**: `ros_monitor` executes `ros2` commands synchronously; timeouts/log output are only partially handled and may block under load.  
  _Next step_: 1) Replace shell-outs with rclpy or cached background collectors; 2) Add resilience (debounce errors, expose status when commands unavailable).
- **Frontend admin mode stub**: UI simulates admin toggling without calling `/api/auth/verify-admin`, risking confusion and inconsistent state.  
  _Next step_: 1) Wire up real API calls with CSRF/cookie handling; 2) Reflect backend session expiry in UI and disable critical actions when not authorized.
- **Deployment script side effects**: `scripts/run_dev.sh` edits `/etc/sudoers.d` and `/usr/share/nginx` directly, which is risky in production.  
  _Next step_: 1) Review script for idempotency and guardrails; 2) Provide alternative containerized or non-sudo dev workflow; 3) Parameterize paths/usernames.
- **Missing automated tests & monitoring**: No unit/integration coverage; failure detection relies on manual checks.  
  _Next step_: 1) Add pytest-based coverage for services and dependencies; 2) Implement integration smoke tests for key APIs; 3) Hook health metrics into deployment pipeline dashboards.
