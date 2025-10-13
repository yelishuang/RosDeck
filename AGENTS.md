# Repository Guidelines

## Project Structure & Module Organization
RosDeck pairs a FastAPI backend in `backend/app/` with a static client under `html/`. Feature routers live in `backend/app/routes/`, shared dependencies in `backend/app/deps/`, services in `backend/app/services/`, and WebSocket handlers in `backend/app/ws/`. Configuration samples reside at `backend/config.example.yaml`, while backend tests belong in `backend/tests/`. Front-end modules load from `html/index.js`, with feature modules inside `html/modules/`, shared widgets in `html/shared/`, and styles and assets next to `html/index.css` and `html/assets/`. Deployment and helper code sit in `nginx/` and `privileged/`, with architecture notes in `docs/`.

## Build, Test, and Development Commands
Use `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` inside `backend/` to prep dependencies. Start the API locally via `uvicorn app.main:app --reload --host 127.0.0.1 --port 4162`. Run the full stack with `scripts/run_dev.sh` (requires sudo for syncing nginx assets and building PAM helpers) and stop it using `scripts/stop.sh`. For UI-only tweaks, serve the static files with `python3 -m http.server` from `html/`. Execute backend tests through `pytest backend/tests`.

## Coding Style & Naming Conventions
Follow PEP 8 with four-space indents, snake_case functions, and PascalCase Pydantic models. Group backend routes by domain (`routes/runtime.py`, `routes/ros.py`) and reuse dependencies under `deps/` to keep CSRF and auth consistent. Prefer descriptive logging via the configuration in `app/main.py`. Front-end JavaScript favors camelCase, jQuery event hooks, and filenames mirroring menu entries (e.g., `html/modules/ros-status.js`).

## Testing Guidelines
Tests leverage Pytest plus `httpx.AsyncClient` to exercise FastAPI endpoints; mirror router names (`test_runtime.py`) and mock privileged helpers or filesystem calls. Aim for deterministic tests and document any manual ROS prerequisites in `docs/`. Use `pytest backend/tests` before submitting changes.

## Commit & Pull Request Guidelines
Commits follow Conventional Commit prefixes with scoped areas (`fix(runtime): guard missing PID`). Squash exploratory work before pushing. Pull requests should explain the change, list validation commands, link related issues, and attach screenshots or terminal captures for UI updates. Flag modifications that require rerunning `scripts/run_dev.sh` so reviewers can rebuild helpers.

## Security & Configuration Tips
Treat `privileged/src/*.c` edits as sensitive—they compile to setuid binaries. Copy `backend/config.example.yaml` when adjusting configuration. Review the nginx paths invoked by `scripts/run_dev.sh` and inspect `/var/log/rosdeck/` after deployments.
