# Repository Guidelines

## Project Structure & Module Organization
RosDeck pairs a FastAPI backend with a static web client. Backend code is in `backend/app/` (feature routers in `routes/`, shared deps in `deps/`, services in `services/`, WebSockets in `ws/`). Sample config sits at `backend/config.example.yaml`; Python deps live in `backend/requirements.txt`. Front-end assets stay under `html/` with `index.js` launching modules inside `modules/`, shared widgets in `shared/`, and styles/assets alongside `index.css` and `assets/`. Deployment support files are in `nginx/` and the privileged helper sources in `privileged/`; architectural notes live in `docs/`.

## Build, Test, and Development Commands
Inside `backend/`, create a virtualenv and install packages (`python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`). Start the API with `uvicorn app.main:app --reload --host 127.0.0.1 --port 4162`. For a full stack, run `scripts/run_dev.sh` (requires sudo to sync static files into nginx and to build the PAM helpers); stop services via `scripts/stop.sh`. During UI work you can serve `html/` with a simple `python3 -m http.server` and point the browser to `index.html`.

## Coding Style & Naming Conventions
Follow PEP 8 in backend modules: four-space indents, snake_case functions, PascalCase Pydantic models, and route modules grouped by domain (e.g., `ros`, `runtime`). Reuse dependency injection primitives under `deps/` so CSRF enforcement stays consistent. Log through the shared `logging` configuration in `app/main.py`. Front-end JavaScript favors camelCase, jQuery-powered event handlers, and filenames that mirror menu entries (`modules/ros-*`). Keep static filenames lowercase with hyphens.

## Testing Guidelines
Add backend tests under `backend/tests/` with Pytest plus `httpx.AsyncClient` to hit FastAPI endpoints; align module names with the router under test (`test_ros_config.py`, `test_network.py`). Mock privileged helpers and file paths when possible, and document any manual ROS integration steps in `docs/` if automation is impractical. UI changes should ship with reproducible browser test steps or lightweight DOM assertions.

## Commit & Pull Request Guidelines
Commits follow Conventional Commit prefixes (`feat:`, `fix:`, `chore:`) and mention the touched area (`feat(runtime): add restart hook`). Squash noisy interim work before pushing. Pull requests must outline the change, list validation commands, link related issues, and attach screenshots or terminal captures for UX updates. Flag any modifications that require rerunning `scripts/run_dev.sh` so reviewers can rebuild helpers.

## Security & Configuration Tips
Treat `privileged/src/*.c` changes carefully—they compile into setuid binaries. Work from copies of `backend/config.example.yaml` rather than editing the template. Review nginx paths in `scripts/run_dev.sh` before syncing, and check `/var/log/rosdeck/` after deployments for unexpected warnings.
