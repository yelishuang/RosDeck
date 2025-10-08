#!/usr/bin/env bash
# scripts/stop.sh
# 作用：停止 RosDeck 后端服务
set -euo pipefail

# ===== 颜色日志 =====
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_i(){ echo -e "${GREEN}[INFO]${NC} $*"; }
log_w(){ echo -e "${YELLOW}[WARN]${NC} $*"; }
log_e(){ echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ===== 配置 =====
PIDFILE="/run/rosdeck-backend.pid"
BACKEND_PORT=4162

echo -e "${YELLOW}========== 停止 RosDeck ===========${NC}"

# ===== 1. 从 PID 文件停止 =====
if [[ -f "$PIDFILE" ]]; then
  PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]]; then
    if ps -p "$PID" >/dev/null 2>&1; then
      log_i "停止后端进程 (PID=$PID)"
      sudo kill "$PID" 2>/dev/null || kill "$PID" 2>/dev/null || true
      sleep 1
      
      # 强制杀掉
      if ps -p "$PID" >/dev/null 2>&1; then
        log_w "进程未响应，强制停止"
        sudo kill -9 "$PID" 2>/dev/null || kill -9 "$PID" 2>/dev/null || true
      fi
      
      log_i "后端进程已停止"
    else
      log_w "PID 文件存在但进程不存在 (PID=$PID)"
    fi
  fi
  
  # 删除 PID 文件
  sudo rm -f "$PIDFILE"
else
  log_w "未找到 PID 文件: $PIDFILE"
fi

# ===== 2. 停止所有 uvicorn 进程 (备用) =====
UVICORN_PIDS=$(pgrep -f "uvicorn.*app.main:app" || true)
if [[ -n "${UVICORN_PIDS:-}" ]]; then
  log_i "发现残留的 uvicorn 进程，正在停止..."
  echo "$UVICORN_PIDS" | while read -r pid; do
    log_i "停止进程 PID=$pid"
    kill "$pid" 2>/dev/null || sudo kill "$pid" 2>/dev/null || true
  done
  sleep 1
  
  # 强制清理
  REMAINING=$(pgrep -f "uvicorn.*app.main:app" || true)
  if [[ -n "${REMAINING:-}" ]]; then
    log_w "强制停止残留进程"
    pkill -9 -f "uvicorn.*app.main:app" || true
  fi
fi

# ===== 3. 检查端口占用 =====
PORT_PID=$(sudo lsof -ti:$BACKEND_PORT 2>/dev/null || true)
if [[ -n "${PORT_PID:-}" ]]; then
  log_w "端口 $BACKEND_PORT 仍被占用 (PID=$PORT_PID)，正在释放..."
  sudo kill -9 "$PORT_PID" 2>/dev/null || true
fi

# ===== 4. 验证 =====
sleep 0.5
if pgrep -f "uvicorn.*app.main:app" >/dev/null 2>&1; then
  log_e "警告：仍有 uvicorn 进程在运行"
  ps aux | grep uvicorn | grep -v grep
  exit 1
fi

if sudo lsof -ti:$BACKEND_PORT >/dev/null 2>&1; then
  log_e "警告：端口 $BACKEND_PORT 仍被占用"
  sudo lsof -i:$BACKEND_PORT
  exit 1
fi

# ===== 完成 =====
echo -e "${GREEN}========== RosDeck 已停止 ===========${NC}"
log_i "后端进程已全部停止"
log_i "端口 $BACKEND_PORT 已释放"
echo -e "${GREEN}=====================================${NC}"
