#!/usr/bin/env bash
# scripts/run_dev.sh
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
need_cmd cc
if ! command -v systemctl >/dev/null 2>&1; then log_w "未检测到 systemd，需手动管理 Nginx 与后台进程"; fi

log_i "项目根目录：$PROJECT_ROOT"

# ===== 安装认证助手 =====
HELPER_SRC="$PROJECT_ROOT/privileged/rosdeck_auth_helper.c"
HELPER_BIN="/usr/local/libexec/rosdeck-auth-helper"
CONTROL_SRC="$PROJECT_ROOT/privileged/src/rosdeck_control_helper.c"
CONTROL_BIN="/usr/local/libexec/rosdeck-control-helper"
LEGACY_POWER_BIN="/usr/local/libexec/rosdeck-power-helper"

if [[ -f "$HELPER_SRC" ]]; then
  INSTALL_HELPER=false
  if [[ ! -x "$HELPER_BIN" ]]; then
    INSTALL_HELPER=true
  elif [[ "$HELPER_SRC" -nt "$HELPER_BIN" ]]; then
    INSTALL_HELPER=true
  fi

  if [[ "$INSTALL_HELPER" == true ]]; then
    log_i "编译安装认证助手"
    sudo mkdir -p "$(dirname "$HELPER_BIN")"
    sudo cc "$HELPER_SRC" -o "$HELPER_BIN" -O2 -Wall -Wextra -lpam -lpam_misc
    sudo chown root:root "$HELPER_BIN"
    sudo chmod 4755 "$HELPER_BIN"
    log_i "认证助手已安装：$HELPER_BIN"
  else
    log_i "认证助手已存在且为最新"
  fi
else
  log_w "未找到认证助手源文件：$HELPER_SRC"
fi

# 安装系统控制助手（继承电源控制能力，可扩展更多系统操作）
if [[ -f "$CONTROL_SRC" ]]; then
  INSTALL_CONTROL=false
  if [[ ! -x "$CONTROL_BIN" ]]; then
    INSTALL_CONTROL=true
  elif [[ "$CONTROL_SRC" -nt "$CONTROL_BIN" ]]; then
    INSTALL_CONTROL=true
  fi

  if [[ "$INSTALL_CONTROL" == true ]]; then
    log_i "编译安装系统控制助手"
    sudo mkdir -p "$(dirname "$CONTROL_BIN")"
    sudo cc "$CONTROL_SRC" -o "$CONTROL_BIN" -O2 -Wall -Wextra
    sudo chown root:root "$CONTROL_BIN"
    sudo chmod 4755 "$CONTROL_BIN"
    log_i "系统控制助手已安装：$CONTROL_BIN"
    if [[ -e "$LEGACY_POWER_BIN" && ! -L "$LEGACY_POWER_BIN" ]]; then
      log_w "检测到旧版控制助手（原电源助手），可根据需要手动移除：$LEGACY_POWER_BIN"
    fi
    sudo ln -sf "$CONTROL_BIN" "$LEGACY_POWER_BIN"
  else
    log_i "系统控制助手已存在且为最新"
    sudo ln -sf "$CONTROL_BIN" "$LEGACY_POWER_BIN"
  fi
else
  log_w "未找到系统控制助手源文件：$CONTROL_SRC"
fi

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

# 确保 Python 包结构完整
log_i "初始化 Python 包结构"
touch "$BACKEND_DIR/app/services/__init__.py"
touch "$BACKEND_DIR/app/models/__init__.py"
touch "$BACKEND_DIR/app/ws/__init__.py"

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
sleep 5  # ✅ 延长等待时间
MAX_RETRY=5
RETRY_COUNT=0

while [ $RETRY_COUNT -lt $MAX_RETRY ]; do
  if curl -fsS "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
    log_i "后端健康检查通过"
    break
  fi
  RETRY_COUNT=$((RETRY_COUNT + 1))
  if [ $RETRY_COUNT -lt $MAX_RETRY ]; then
    sleep 1
  fi
done


# ===== 汇总 =====
echo
echo -e "${GREEN}========== RosDeck 已启动 ==========${NC}"
echo "前端： http://localhost:${FRONTEND_PORT}/"
echo "登录： http://localhost:${FRONTEND_PORT}/auth/login.html"
echo "后端： http://${BACKEND_HOST}:${BACKEND_PORT}/api/health"
echo "日志： $BACKEND_LOG"
echo "PID ： $(cat "$PIDFILE" 2>/dev/null || echo '-')"
echo -e "${GREEN}=====================================${NC}"
