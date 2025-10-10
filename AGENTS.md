# RosDeck Agent Handbook（2024Q2 更新）

为自动化模型 / 代码代理提供 RosDeck 项目速览，便于快速接手开发与排查工作。

---

## 1. 项目概览
- **目标**：在局域网内自托管 ROS 2 设备控制台，提供系统/网络/存储监控、文件与终端访问、日志查看、进程与服务管理，并为“前端–ROS–大模型”协同打基础。
- **技术栈**
  - 后端：FastAPI + Uvicorn（Python 3.11），强化 PSUtil、systemd、PAM、journalctl、smartctl 等系统能力；
  - 前端：Nginx 托管的静态 HTML/CSS/JS，模块化加载（jQuery 风格）+ Chart.js + Toastr + Bootstrap；
  - 特权操作：setuid C helper 完成 PAM 校验、电源控制；Python 服务通过 `mount` / `mkfs.*` / `systemctl` 等命令行工具执行高危操作。
- **最新亮点**
  - ✅ **存储管理模块**（Storage）：展示磁盘/分区使用情况、历史趋势、报表导出；管理员可执行白名单清理、挂载、分区/格式化、SMART 自检及报告。
  - ✅ **运行中心模块**（Runtime）：实时查看进程与 systemd 服务，支持管理员结束进程、启停/重启/启用/禁用服务。
  - ✅ 日志、网络等模块功能完备，可直接依赖。

---

## 2. 目录速览
| 路径 | 说明 |
| --- | --- |
| `backend/app/main.py` | FastAPI 入口；注册全部路由、中间件、健康检查。 |
| `backend/app/routes/` | 业务路由：`auth`、`system`、`device`、`ros`、`files`、`terminal`、`logs`、`network`、`storage`、`runtime`。 |
| `backend/app/services/` | 服务层：系统/ROS 监控、特权操作、journalctl、网络监控、存储监控与操作、进程监控、systemd 服务管理。 |
| `backend/app/deps/` | 依赖注入：CSRF token、管理员 session、登录限流。 |
| `html/` | 前端入口与模块资源（`modules/overview`、`modules/storage`、`modules/runtime`、`modules/logs` 等）。 |
| `privileged/` | setuid C helper 源码（PAM 验证、电源控制）。 |
| `scripts/` | 启动/停止脚本。 |
| `nginx/rosdeck.nginx.conf` | Nginx 反向代理配置。 |
| `docs/ARCHITECTURE.md` | 仍为空白，待补充。 |

---

## 3. 启动方式
1. **全栈开发（需 sudo）**
   ```bash
   scripts/run_dev.sh
   ```
   - 编译/安装 helper → `/usr/local/libexec/`；
   - 同步前端至 `/usr/share/nginx/html/rosdeck`；
   - 安装 Nginx 配置、创建虚拟环境、安装依赖；
   - 启动 backend（127.0.0.1:4162），完成健康检查。
2. **仅后端（无 sudo）**
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload --host 127.0.0.1 --port 4162
   ```
   前端可通过 `cd html && python -m http.server 8000` 静态调试。
3. **停止服务**
   ```bash
   scripts/stop.sh
   ```
   根据 PID / 端口清理 Uvicorn。

---

## 4. 后端概述
- `app/main.py`
  - 配置 CORS（允许 `http://localhost:1221`）；
  - 请求日志中间件、健康检查 `/api/health`、CSRF 获取 `/api/csrf-token`；
  - 挂载全量业务路由。

- **核心路由**
  - `routes/auth.py`：基于 PAM 的登录、管理员模式验证、登出；内存限流。
  - `routes/system.py`：系统状态、重启/关机（管理员 + CSRF）。
  - `routes/device.py`：设备信息（主机名、架构、发行版）。
  - `routes/ros.py`：ROS 指标（节点/Topic/Service 数），无 ROS 环境时返回默认值。
  - `routes/files.py`：目录浏览、上传（50MB）、下载、删除。普通用户限制在 `$HOME`，管理员全局访问。
  - `routes/terminal.py`：WebSocket 交互 shell，含黑名单/超时策略。
  - `routes/logs.py`：journalctl 查询（优先级/关键字/时间范围/游标），含管理员放宽限制。
  - `routes/network.py`：网络接口监控、流量历史、连接统计及管理员网络配置操作。
  - `routes/storage.py`：磁盘总览、分区列表、报表导出，管理员执行清理/挂载/分区/SMART，自带操作日志。
  - `routes/runtime.py`：进程列表（排序/过滤）、系统服务状态；管理员可终止进程、控制 systemd 服务。

- **服务层重点**
  - `services/system_monitor.py`：psutil 采样 + 2 秒缓存。
  - `services/network_monitor.py`：接口流量 + 180 点历史数据。
  - `services/journalctl_reader.py`：封装 journalctl 参数、权限提示。
  - `services/storage_monitor.py`：维护 10 分钟滑动窗口（5s 间隔），返回总览、分区及 IO 速率。
  - `services/storage_operations.py`：提供白名单清理、`mount/umount`、`mkfs/wipefs`、`smartctl` 操作；全量日志记录并回传 `log_id`。
  - `services/process_monitor.py`：两段式采样 CPU/内存，支持排序及安全终止。
  - `services/service_manager.py`：调用 `systemctl` 获取服务列表、状态与启停操作（调用 `sudo systemctl`，需要后端进程具备免密权限）。
  - 其它：`ros_monitor.py`、`terminal_manager.py`、`admin_privileged.py` 等参考旧版说明。

- **依赖/安全要点**
  - 管理员模式与 CSRF 令牌均为 **内存态**，服务重启即失效；多实例部署需外部存储（Redis/SQLite）。
  - `storage_operations` 默认假定后端以 root 或具有足够权限运行；危险操作含倒计时与路径校验，但仍需人工确认。
  - Runtime 模块执行 `sudo systemctl`，需配置 `rosdeck` 运行用户的 sudo 免密码规则，否则操作失败。

---

## 5. 前端概述
- **入口**：`html/index.html` + `index.js`
  - 侧边栏模块化加载（`loadModule`）；顶部状态栏轮询 `/api/system/status`；
  - 管理员模式按钮触发 `/api/auth/verify-admin`，全局广播事件 `rosdeck:admin-mode-change`；
  - 统一管理 CSRF token（`sessionStorage`）；Toastr 展示提示。

- **主要模块**
  - `modules/overview/`：系统/ROS 概览卡片，轮询后端数据。
  - `modules/file-transfer/`：文件树、上传、下载、删除；管理员模式切换目录范围。
  - `modules/terminal/`：基于 xterm.js 的 Web 终端，含主题、历史、黑名单。
  - `modules/logs/`：图形化筛选器 + 深色日志展示，支持加载更多/刷新/权限提示。
  - `modules/network/`：接口列表、流量图、连接详情、IP 配置与诊断。
  - `modules/storage/`：磁盘环形图、历史趋势、报表导出、管理员操作面板（倒计时确认、操作日志、SMART 报告）。
  - `modules/runtime/`：进程/服务 Tab；支持搜索、排序、自动刷新、管理员操作按钮动态显隐。

- **公共依赖**
  - jQuery、Bootstrap 5、Toastr、Chart.js（全局）、xterm.js（终端模块按需加载）。
  - 样式基于 `html/index.css` 与各模块 `style.css`。

---

## 6. 特权与系统依赖
- `privileged/rosdeck_auth_helper.c`：PAM 验证指定用户（默认 root），需 setuid 安装。
- `privileged/src/rosdeck_control_helper.c`：通过 systemctl 调用关机/重启。
- Python 服务侧依赖系统命令：
  - `mount` / `umount` / `mkfs.*` / `wipefs`；
  - `smartctl`（SMART 功能）；
  - `systemctl`、`journalctl`、`ip`、`ping`；
  - 需确保运行用户具备相应能力，必要时配置 sudoers。

---

## 7. 部署脚本
- `scripts/run_dev.sh`
  - 检查系统依赖（nginx/rsync/python3/curl/sed/awk/cc/smartctl）；
  - 编译并安装 helper、同步前端、部署 Nginx、创建 venv、安装依赖、启动 back-end；
  - 最后调用 `/api/health` 验证。
- `scripts/stop.sh`
  - 通过 PID/端口停止 Uvicorn，清理锁文件。

---

## 8. 日志与排错
- 后端日志：`tail -f /var/log/rosdeck/backend.log`
- 健康检查：`curl -fsS http://127.0.0.1:4162/api/health`
- 端口占用：`ps aux | grep uvicorn`、`ss -ltnp | grep 4162`
- **模块排查建议**
  - 文件/终端：确认 Cookie 中 `session_id` 存在；管理员操作需 `admin_session_id`。
  - 日志模块：若提示无权限，将运行用户加入 `systemd-journal` 组或使用 sudo wrapper。
  - 网络模块：若静态 IP 配置失败，校验 `netplan`、`ip` 可用性与管理员模式；Chart.js 报错多为资源未加载。
  - 存储模块：
    - SMART 功能需安装 `smartctl` 并允许访问设备；
    - 挂载/格式化操作失败时检查运行用户权限与目标设备状态；
    - 报表导出只读 HTTP，需浏览器允许下载。
  - 运行中心：
    - 进程终止/服务控制需管理员模式 + CSRF；
    - `sudo systemctl` 若要求密码，请调整 `/etc/sudoers`。

---

## 9. 已知风险 / 待办
1. **Session/CSRF 安全**：session 与 CSRF Token 均存于内存，服务重启即失效；尚不支持分布式/持久化。
2. **高危命令安全**：
   - 存储模块虽有白名单与倒计时，但仍需人工确认；建议引入命令审计与细粒度权限。
   - `systemctl`、`mount` 等操作依赖环境配置，需在部署文档中明确 sudo 权限。
3. **日志与操作追踪**：操作日志仅缓存在内存（最多 100 条），缺少持久化与告警提示。
4. **自动化测试缺失**：后端无 pytest，前端无单元/端到端测试。建议为存储、运行中心等核心模块补充测试。
5. **文档滞后**：`docs/ARCHITECTURE.md` 仍空白；部署/安全策略未形成正式说明。
6. **多语言/国际化**：界面与提示均为中文，若需国际化需额外投入。

---

## 10. 新接手者 Checklist
1. 阅读本手册、`backend/app/main.py`、`html/index.js`，理解路由与前端模块化机制。
2. 在受控环境执行 `scripts/run_dev.sh`，确认前端 + 后端 + Nginx 正常；若无 sudo，可按“仅后端”步骤搭建。
3. 熟悉 `privileged/` helper 的编译与权限设置，了解 sudo / setuid 风险。
4. 针对目标改动定位对应模块（后端路由 + 服务、前端模块），梳理管理员权限与 CSRF 依赖。
5. 若涉及高危操作（格式化、systemctl 等），先在测试环境验证，并更新操作日志/审计策略。
6. 编写或更新文档与测试用例（尤其是新增接口/前端交互），保持实现与文档同步。
7. 记录手动验证步骤；如需 sudo 或影响系统行为，请在 PR/说明中明确说明风险与回滚方案。

---

保持本手册与代码同步：当架构、权限策略或系统依赖变更时，请同步更新 `AGENTS.md` 与 `docs/ARCHITECTURE.md`，确保后续模型/开发者能够快速上手。***
