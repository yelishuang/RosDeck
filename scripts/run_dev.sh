#!/usr/bin/env bash
# scripts/deploy_and_start.sh
# 作用：将前端同步到 Nginx 根目录（按你既有方案），安装/校验/启动 Nginx（1221 端口）；
#      准备并后台启动后端（4162 端口），写入独立日志与 PID。
set -euo pipefail

# ===== 配置区 =====
FRONTEND_PORT=1221
BACKEND_HOST=127.0.0.1
BACKEND_PORT=4162
NGINX_ROOT="/usr/share/nginx/html/rosdeck"                # 仅同步静态文件；不改你的逻辑
NGINX_CONF_DST="/etc/nginx/conf.d/rosdeck.conf"           # 你的现有 Nginx 站点配置路径
LOG_DIR="/var/log/rosdeck"
BACKEND_LOG="$LOG_DIR/backend.log"
PIDFILE="/run/rosdeck-backend.pid"

# ===== 颜色日志 =====
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_i(){ echo -e "${GREEN}[INFO]${NC} $*"; }
log_w(){ echo -e "${YELLOW}[WARN]${NC} $*"; }
log_e(){ echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ===== 路径计算 =====
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
VENV_DIR="$BACKEND_DIR/.venv"
NGINX_CONF_SRC="$PROJECT_ROOT/nginx/rosdeck.nginx.conf"

# ===== 工具检测 =====
need_cmd(){ command -v "$1" >/dev/null 2>&1 || { log_e "缺少命令：$1"; exit 1; }; }
need_cmd nginx
need_cmd rsync
need_cmd python3
need_cmd curl
need_cmd sed
need_cmd awk
if ! command -v systemctl >/dev/null 2>&1; then log_w "未检测到 systemd，需手动管理 Nginx 与后台进程"; fi

log_i "项目根目录：$PROJECT_ROOT"

# ===== 同步前端到 Nginx 路径（不改变你的既有逻辑）=====
log_i "同步前端到 ${NGINX_ROOT}"
sudo mkdir -p "$NGINX_ROOT"
sudo rsync -a --delete --exclude='*.md' --exclude='.gitkeep' "$PROJECT_ROOT/html/" "$NGINX_ROOT/"

# 识别 Nginx 运行用户（尽量兼容各发行版）
NGINX_USER="$(awk '/^user[[:space:]]/{print $2}' /etc/nginx/nginx.conf 2>/dev/null | sed 's/;//' || true)"
if [[ -z "${NGINX_USER:-}" ]]; then
  if id -u nginx >/dev/null 2>&1; then NGINX_USER="nginx"; else NGINX_USER="www-data"; fi
fi
sudo chown -R "$NGINX_USER":"$NGINX_USER" "$NGINX_ROOT" || true
sudo find "$NGINX_ROOT" -type d -exec chmod 755 {} \; || true
sudo find "$NGINX_ROOT" -type f -exec chmod 644 {} \; || true

# ===== 安装/校验/启动 Nginx（按你已有配置）=====
if [[ -f "$NGINX_CONF_SRC" ]]; then
  log_i "安装 Nginx 配置到 ${NGINX_CONF_DST}"
  sudo cp "$NGINX_CONF_SRC" "$NGINX_CONF_DST"
else
  log_w "未找到仓库内 Nginx 配置（$NGINX_CONF_SRC），跳过覆盖"
fi

sudo nginx -t >/dev/null
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl enable --now nginx >/dev/null
  sudo systemctl reload nginx >/dev/null || true
else
  sudo nginx -s reload >/dev/null 2>&1 || sudo nginx
fi
log_i "前端就绪： http://localhost:${FRONTEND_PORT}/  （登录页：/auth/login.html）"

# ===== 后端：准备虚拟环境与依赖 =====
if [[ ! -d "$VENV_DIR" ]]; then
  log_i "创建后端虚拟环境：$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
log_i "安装后端依赖（静默）"
pip -q install --upgrade pip
if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
  pip -q install -r "$BACKEND_DIR/requirements.txt"
else
  log_w "未找到 $BACKEND_DIR/requirements.txt，跳过依赖安装"
fi

# ===== 后端：后台启动（独立日志与 PID）=====
sudo mkdir -p "$LOG_DIR"
sudo touch "$BACKEND_LOG"
sudo chown "$(id -u)":"$(id -g)" "$BACKEND_LOG"

# 停旧进程
if [[ -f "$PIDFILE" ]]; then
  OLD_PID="$(cat "$PIDFILE" || true)"
  if [[ -n "${OLD_PID:-}" ]] && ps -p "$OLD_PID" >/dev/null 2>&1; then
    log_i "停止已有后端进程 (PID=$OLD_PID)"
    kill "$OLD_PID" || true
    sleep 0.5
  fi
fi

log_i "后台启动后端：${BACKEND_HOST}:${BACKEND_PORT}（日志：$BACKEND_LOG）"
(
  cd "$BACKEND_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  # 以后台方式启动，日志写入文件
  nohup uvicorn app.main:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    --log-level info \
    >>"$BACKEND_LOG" 2>&1 &
  echo $! > /tmp/rosdeck-backend.pid.$$
)
NEW_PID="$(cat /tmp/rosdeck-backend.pid.$$)"; rm -f /tmp/rosdeck-backend.pid.$$
echo "$NEW_PID" | sudo tee "$PIDFILE" >/dev/null
log_i "后端已启动 (PID=$NEW_PID)"

# ===== 健康检查（简短，不通过仅提示）=====
sleep 0.6
if curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
  log_i "后端健康检查通过"
else
  log_w "后端健康检查未通过，请查看日志：$BACKEND_LOG"
fi

# ===== 汇总 =====
echo
echo -e "${GREEN}========== RosDeck 已启动 ==========${NC}"
echo "前端： http://localhost:${FRONTEND_PORT}/"
echo "登录： http://localhost:${FRONTEND_PORT}/auth/login.html"
echo "后端： http://${BACKEND_HOST}:${BACKEND_PORT}/api/health"
echo "日志： $BACKEND_LOG"
echo "PID ： $(cat "$PIDFILE" 2>/dev/null || echo '-')"
echo -e "${GREEN}=====================================${NC}"
