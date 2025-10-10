# RosDeck Agent Handbook

> 给自动化模型 / 代码代理的速览文档。读完本手册，即可理解当前代码库的结构、约定、风险点与下一步行动。

---

## 1. 项目概览
- **愿景**：构建局域网内可自托管的控制台，监控运行 ROS 2 的开发板/边缘设备，提供轻量的系统运维能力（监控、终端、文件传输等），并逐步扩展到 “前端 – ROS – 大模型” 协同操作。
- **核心特性**
  - FastAPI 后端暴露系统/ROS 指标、认证、终端、文件 API。
  - 静态前端（`html/`）通过 Nginx 服务，可模块化加载页面。
  - setuid C helper 处理 root 密码校验和关机/重启。
  - 脚本同步前端、部署 Nginx、启动后端并输出日志。
- **当前状态**：后端路由与主要服务已实装；前端概览、文件传输、终端模块可用，其余页面大多仍为占位。文档与安全机制尚存缺口（见 §11）。

---

## 2. 目录导览

| 路径 | 说明 |
| --- | --- |
| `backend/app/main.py` | FastAPI 入口，挂载路由、中间件、健康检查、CSRF 下发。 |
| `backend/app/routes/` | 认证、系统状态、ROS 指标、设备信息、文件传输、终端 WebSocket。 |
| `backend/app/services/` | `SystemMonitor`、`ROSMonitor`、特权操作、终端会话管理等服务。 |
| `backend/app/deps/` | CSRF 校验、管理员 Session 管理、登录限流、公用依赖。 |
| `backend/config.example.yaml` | 预留配置示例，占位文件。 |
| `html/` | 前端静态资源主目录，入口 `index.html`/`index.js`。 |
| `html/modules/*` | 功能模块（概览、终端、文件传输、ROS 等）；部分仍为占位。 |
| `privileged/` | PAM 验证与系统控制 helper 的 C 源码。 |
| `scripts/run_dev.sh` | 开发部署脚本：编译 helper、同步前端、配置/启动 Nginx 和后端。 |
| `scripts/stop.sh` | 停止后端、清理端口的脚本。 |
| `nginx/rosdeck.nginx.conf` | Nginx 反向代理配置，负责 Session 保护与 WS 转发。 |
| `docs/ARCHITECTURE.md` | 仍未更新，待按本文手册补全。 |

---

## 3. 环境准备
- **操作系统**：Linux（已验证 openEuler RISC-V）。需预装 `nginx`、`python3`、`rsync`、`cc`、PAM 相关库。
- **Python**：建议 3.11。后端默认使用 `backend/.venv` 虚拟环境。
- **ROS 指标**：若需启用，需在同一 Python 环境安装 `rclpy` 并配置好 `ROS_DISTRO`。

---

## 4. 启动与停止

### 4.1 全栈开发模式（需 sudo）
```bash
cd /path/to/RosDeck
scripts/run_dev.sh
```
脚本行为：
1. 编译 setuid helper 至 `/usr/local/libexec/` 并确保权限。
2. 同步 `html/` → `/usr/share/nginx/html/rosdeck`，覆盖 Nginx 配置到 `/etc/nginx/conf.d/rosdeck.conf`。
3. 创建/更新 `backend/.venv`，安装 `requirements.txt`。
4. 以 `nohup uvicorn` 启动后端 (`127.0.0.1:4162`)，日志写入 `/var/log/rosdeck/backend.log`，PID 记录在 `/run/rosdeck-backend.pid`。
5. 自动执行健康检查。
6. 前端访问 `http://localhost:1221/`，登录页 `/auth/login.html`。

### 4.2 仅启动后端（无 sudo）
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 4162
```
静态页面可使用 `python -m http.server` 单独调试。

### 4.3 停止服务
```bash
scripts/stop.sh
```
脚本依据 PID 文件和进程特征停止 uvicorn，清理端口 4162。

---

## 5. 后端架构

### 5.1 应用入口
- `backend/app/main.py`
  - 初始化 FastAPI，配置 CORS（允许 `http://localhost:1221`）。
  - 注册请求日志中间件。
  - 挂载路由：`auth`, `system`, `ros`, `device`, `files`, `terminal`。
  - `/api/metrics` 记录关键事件；`/api/health` 返回 `{status: ok}`。
  - `/api/csrf-token` 下发全局 CSRF 值；`/auth/login.html` 去磁盘读取登录页并注入 meta token。

### 5.2 路由总览
- `routes/auth.py`
  - `/api/auth/login`：PAM 验证 Linux 用户，发放 `session_id` Cookie（格式 `session_{username}_{timestamp}`）。
  - `/api/auth/logout`：清空 session/admin cookie。
  - `/api/auth/verify-admin`：调用特权 helper 校验 root 密码，生成 `admin_session_id`（30 分钟 TTL）。
  - `/api/auth/admin-logout`：撤销管理员模式。
- `routes/system.py`
  - `/api/system/status`：调用 `SystemMonitor` 返回 uptime/CPU/内存/磁盘/网络（2 秒缓存）。
  - `/api/system/power`：管理员+CSRF 保护，调用控制 helper 执行 reboot/shutdown。
- `routes/device.py`
  - `/api/device/info`：主机名、OS（含 openEuler 检测）、架构、LAN IP。
- `routes/ros.py`
  - `/api/ros/stats`：从后台 ROSMonitor 读取节点/话题/服务数量与稳定度估计。
- `routes/files.py`
  - `/api/files/list`：列目录，普通用户根目录为 `~`，管理员为 `/`。支持面包屑与越权检测。
  - `/api/files/upload`：50 MB 限制（流式写入），禁止覆盖同名文件。
  - `/api/files/download`：返回文件内容。
  - `/api/files` `DELETE`：删除文件（不支持目录）。
- `routes/terminal.py`
  - `/api/terminal/ws`：基于 Cookie 验证用户 / 管理员身份，启动 PTY，转发输入输出。
  - `/api/terminal/history`：返回当前登录用户的命令历史（若会话仍在）。
  - `/api/terminal/session-info`：查询 admin 模式剩余时间等信息。

### 5.3 服务层
- `services/system_monitor.py`：封装 psutil 指标，互斥缓存。
- `services/ros_monitor.py`：后台线程维护 rclpy 节点；若 rclpy 不可用，则日志告警并返回默认值。
- `services/admin_privileged.py`：调用 helper 校验 root 密码或执行 reboot/shutdown。
- `services/terminal_manager.py`：
  - `TerminalSession`：包装 PTY，管理命令历史、超时（30 min 无活动即关闭）。
  - `TerminalManager`：全局会话表、命令黑名单（`rm -rf /` 等）与管理员专属命令检测。

### 5.4 依赖模块
- `deps/csrf.py`：全局 CSRF token（内存存储，identifier 固定为 `"global"`）。
- `deps/admin_auth.py`：管理员 session 管理（内存字典 + TTL，提供 `require_admin` 依赖）。
- `deps/rate_limit.py`：登录与管理员验证的 IP 级限流（默认 5 次/5 分钟），使用内存队列。
- `deps/admin_auth.extract_username_from_session`：解析 `session_id`，按最后一个 `_` 分割时间戳。

### 5.5 配置文件
- `backend/config.example.yaml`、`backend/app/config.py` 目前仅作为目录占位。

---

## 6. 前端架构

### 6.1 主结构
- 入口页面：`html/index.html`，顶部状态栏 + 侧边栏（模块导航、管理员模式按钮、重启菜单）。
- 主逻辑：`html/index.js`
  - 页面加载后：拉取/缓存 CSRF、绑定侧边栏事件、启动状态轮询。
  - `loadModule(modulePath)`：加载 `modules/{modulePath}/index.html` + 样式 + JS（若存在）。
  - 状态栏：每 5 秒调用 `/api/system/status`，更新 uptime/CPU/内存/磁盘/网络。
  - 设备卡片：每 60 秒调用 `/api/device/info`。
  - 管理员模式：`/api/auth/verify-admin` / `/api/auth/admin-logout`，验证成功后广播 `rosdeck:admin-mode-change`。
  - 电源控制：提交 `/api/system/power`，需管理员模式。

### 6.2 模块模式
- 每个模块包含 `index.html` / `main.js` / `style.css`。
- 约定 `window.moduleInit()` / `window.moduleCleanup()` 生命周期，`index.js` 在模块切换时自动调用。
- 全局样式在 `html/shared/css/common.css`，给占位模块使用。

### 6.3 已实现重点模块
- `modules/overview/`
  - 轮询 `/api/ros/stats`，展示节点/话题/服务/稳定度。
- `modules/file-transfer/`
  - 支持目录浏览、上传（多文件顺序上传）、下载、删除。
  - 监听 `rosdeck:admin-mode-change`，管理员模式激活时更新根目录权限提示。
  - 调用后端 API 时默认携带 Cookie；上传使用表单的 `UploadFile`。
- `modules/terminal/`
  - 使用 xterm.js（懒加载 `libs/xterm/xterm.min.js`）。
  - 连接 `/api/terminal/ws`，含重连逻辑、命令历史面板、主题切换（深色/浅色/Dracula/Monokai）、输入缓冲、防抖终端尺寸同步。
  - 检测管理员状态并显示提示；支持命令前置校验（`command_check`）。

### 6.4 仍为占位的模块
- `modules/logs/`, `modules/network/`, `modules/runtime/`, `modules/storage/`, `modules/ros/*` 均仅含占位 HTML，尚未绑定 API。
- 侧边栏用户信息、通知、设备 ID 编辑等仍为静态占位。

---

## 7. Privileged Helpers

### 7.1 `privileged/rosdeck_auth_helper.c`
- PAM 会话验证（默认 root，可传 `--user`）。
- 读取 stdin 密码，调用 `pam_authenticate` + `pam_acct_mgmt`。
- 以 setuid root 安装至 `/usr/local/libexec/rosdeck-auth-helper`。
- 运行前清理环境变量（避免 LD_PRELOAD 等注入）。

### 7.2 `privileged/src/rosdeck_control_helper.c`
- 接受 `reboot` / `shutdown`（以及 `poweroff` 兼容）并调用 `/usr/bin/systemctl`。
- 同样清理关键环境变量，强制 PATH。
- 安装位置 `/usr/local/libexec/rosdeck-control-helper`，并创建兼容链接 `/usr/local/libexec/rosdeck-power-helper`。

> **安全提示**：helper 为 setuid 程序，修改前务必审查输入处理、日志输出、exec 调用路径。

---

## 8. 部署脚本

- `scripts/run_dev.sh`
  - 检查关键工具（nginx/rsync/python3/curl/sed/awk/cc）。
  - 根据源文件时间判断是否需要重新编译 helper。
  - 使用 `rsync` 同步前端，设置文件权限归属 Nginx 用户。
  - 覆盖 Nginx 配置并 reload/enable（若无 systemd 则直接 `nginx -s reload`）。
  - 初始化虚拟环境并安装依赖。
  - 启动后端（nohup），写入日志、PID 文件。
  - 执行健康检查，打印访问信息。
- `scripts/stop.sh`
  - 依据 PID 文件 / `pgrep` 查找 uvicorn 进程，发送 `kill`，必要时 `kill -9`。
  - 检查端口占用并清理，最终确认停止成功。

---

## 9. Nginx 反向代理
- 监听 `1221`，站点根 `/usr/share/nginx/html/rosdeck`。
- `/auth/login.html` 走后端以注入 CSRF。
- 静态资源 (`/auth/`, `/assets/`, `/libs/`, `/modules/`, `/index.html`) 直接读取本地。
- `/api/auth/login`、`/api/health`、`/api/metrics` 在未登录状态下放行。
- 其它 `/api/*`、`/ws/`、`/api/terminal/ws` 强制检查 Cookie 中的 `session_id`，缺失则返回 401/302。
- WebSocket 转发保留 `Upgrade`/`Connection` 头。

---

## 10. 日志与排错
- **后端日志**：`tail -f /var/log/rosdeck/backend.log`（run_dev.sh 启动后自动写入）。
- **进程与端口**：`ps aux | grep uvicorn`、`ss -ltnp | grep 4162`。
- **健康检查**：`curl -fsS http://127.0.0.1:4162/api/health`。
- **ROS 指标缺失**：检查 `rclpy` 是否安装，以及 `ROS_DISTRO` 环境变量。
- **前端无法访问**：确认 Nginx 已 reload、静态资源已同步、Cookie 中包含 `session_id`。

---

## 11. 当前风险与缺口
1. **Session 与 CSRF**
   - `session_id` 仅存于 Cookie，后端没有持久化或校验列表，任何人可伪造 `session_{username}_{timestamp}` 登录态。
   - CSRF token 全局共享，与具体 session 无绑定；任意已登录用户或中间人可复用该 token。
   - 建议后续将 session/CSRF 存储关联到用户（例如 Redis/SQLite），并为登录/管理员操作补充测试。
2. **文档滞后**：`docs/ARCHITECTURE.md` 仍描述“无实现”，需要按本文重写。
3. **前端占位模块**：网络、存储、ROS 子模块仅有占位符，仍需补齐 API 与前端逻辑。
4. **自动化测试**：仓库缺乏 `backend/tests/`；任意新增功能请配合 pytest + FastAPI `TestClient`，特权操作可 mock `subprocess.run`。
5. **硬编码 UI**：主页用户信息/通知等为静态文本，未接入真实数据源。

---

## 12. 测试与质量规范
- **后端**
  - 使用 pytest + FastAPI TestClient（放在 `backend/tests/`，按路由命名，如 `test_system_status.py`）。
  - 对特权调用 mock `subprocess.run` / `pam.pam.authenticate`。
  - 遵循 PEP 8；推荐黑格式（Black）风格。
- **前端**
  - 暂无自动化测试；提交 PR 时附手动验证列表（浏览器版本、步骤、预期结果）。
- **提交信息**：遵循 `type(scope): subject`（例如 `feat(system): add disk stats cache`），72 字符内。

---

## 13. 近期路线图（结合当前状态）
1. **监控强化**
   - 细化系统监控：温度、功耗、磁盘分区等。
   - 完善网络/存储/日志/运行中心模块，串联后端 API。
2. **ROS 深度集成**
   - 丰富 `ROSMonitor`：话题带宽、节点资源消耗、拓扑可视化。
   - 支持基本 ROS 操作（启动/停止节点、服务调用）。
3. **安全与认证增强**
   - 引入真实 session 存储、CSRF 绑定、密码尝试审计。
   - 管理员模式 UI/后端双向同步过期状态。
4. **大模型联动探索**
   - 设计 `modules/ai-console/` 原型；后端预留 LLM 调用骨架（可先 mock）。
   - 设定操作确认机制，确保模型发起的敏感操作需管理员确认。

### Logs 模块（journalctl 方案）现状与计划
- **已落地**
  - 后端：`backend/app/services/journalctl_reader.py` 封装 journalctl 调用，提供优先级/时间/关键词过滤、游标分页与权限探测；`backend/app/routes/logs.py` 暴露 `/api/logs/metadata` 与 `/api/logs/query`，区分管理员与普通用户的行数/优先级限制，并统一错误返回。
  - 前端：`html/modules/logs/` 使用上下卡片布局（顶部筛选、底部日志列表），支持关键字高亮、刷新、加载更多以及管理员模式联动。当前视觉仍待优化，但功能已可查询系统日志。
- **使用前提**
  - 运行环境需具备 `journalctl` 命令且当前用户拥有读取日志的权限（建议加入 `systemd-journal` 组或配置 sudo wrapper）。
  - 若部署在非 systemd 系统，接口会返回不支持提示，后续可考虑提供降级方案。
- **后续迭代方向**
  1. 进一步优化日志结果区域的排版与配色，使页面与整体主题更协调（当前 UI 仍需设计打磨）。
  2. 补充服务/Unit 级别的筛选项，为后续按组件查看日志预留扩展点。
  3. 增加端到端测试：mock `journalctl` 输出验证分页与权限逻辑，确保未来改动安全。
  4. 为普通用户提供更显眼的权限提示与操作指引（如缺少权限时的快速说明）。

## 14. 新接手者 Checklist
1. 阅读本手册 + `backend/app/main.py` + `html/index.js`，了解路由与模块系统。
2. 在受控环境运行 `scripts/run_dev.sh`，确认前后端与 Nginx 正常。
3. 熟悉 `privileged/` helper 编译与权限设置（需 setuid 支持）。
4. 根据目标功能定位对应后端路由/服务或前端模块，补齐缺失逻辑与测试。
5. 记录手动验证步骤；如涉及 sudo/系统改动，在 PR 中明确风险。

---

> **保持文件同步**：当代码结构或安全策略更新时，请同步更新本手册与 `docs/ARCHITECTURE.md`，确保后续模型能快速接力。***
