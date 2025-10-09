# RosDeck Agent 导航手册

> 面向自动化模型 / 代理的速览文档，帮助快速理解当前状态、开发规范与下一步方向。

## 1. 项目愿景
- 目标：构建一个开源的局域网控制台，用于监控运行 ROS 2 的开发板或边缘设备，并提供轻量的服务器管理能力（磁盘/网络监控、文件传输、终端等）。
- 中长期规划：在完善监控和设备管理后，扩展到 “前端 – ROS – 大模型” 的联动交互界面，让大模型可以理解 ROS 资源与实时状态并辅助操作。

## 2. 系统架构速览
- 前端：`html/` 下的静态页面 + jQuery 风格模块化脚本，通过 Nginx 提供服务。
- 后端：`backend/` 内的 FastAPI 应用，负责认证、系统信息、ROS 指标、特权操作。
- 特权助手：`privileged/` 提供的 setuid C 程序，用于经由 PAM 验证 root 密码、执行关机/重启等操作。
- 部署脚本：`scripts/run_dev.sh` 同步静态资源、安装依赖、启动后端并写日志到 `/var/log/rosdeck/backend.log`。
- 反向代理：`nginx/rosdeck.nginx.conf` 负责前后端转发与 Session 检查。

## 3. 快速上手
1. **准备环境**
   - 操作系统：基于 Linux（已在 openEuler RISC-V 上使用），需要 `nginx`, `python3`, `rsync`, `cc`, `pam` 相关库。
   - Python：推荐 Python 3.11（项目默认创建 `backend/.venv`）。
   - 如需 ROS 指标，需要安装 `rclpy` 并在同一环境中可用。
2. **启动（需 sudo）**
   ```bash
   cd /path/to/RosDeck
   scripts/run_dev.sh
   ```
   - 首次运行会提示输入 sudo 密码，并可能写入 `/usr/local/libexec/`、`/etc/nginx/conf.d/`。
   - 前端访问：http://localhost:1221/ ，后端健康检查：http://127.0.0.1:4162/api/health
3. **只启动后端（无 sudo）**
   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload --host 127.0.0.1 --port 4162
   ```
   - 静态页面可通过 `python -m http.server` 等方式单独调试。
4. **停止服务**
   ```bash
   scripts/stop.sh
   ```

## 4. 目录概览
| 路径 | 说明 |
| --- | --- |
| `backend/app/main.py` | FastAPI 入口，注册路由、中间件，提供 `/api/health` 等接口 |
| `backend/app/routes/` | 认证(`auth`)、系统(`system`)、ROS(`ros`)、设备(`device`) 四类路由 |
| `backend/app/services/` | 系统监控、ROS 监控、特权操作封装 |
| `backend/app/deps/` | CSRF、防爆破限流、管理员 Session 等依赖 |
| `backend/config.example.yaml` | 默认配置示例（启动脚本可引用） |
| `html/` | 前端静态资源（入口 `index.html` / `index.js`，模块放在 `modules/`） |
| `html/modules/*` | 功能子页面（概览、网络、运行时、文件传输等） |
| `privileged/` | PAM 验证与系统控制的 C 源码与说明 |
| `scripts/run_dev.sh` | 开发模式启动脚本（同步前端 + 启动后端 + 健康检查） |
| `scripts/stop.sh` | 停止后端与清理端口的脚本 |
| `nginx/rosdeck.nginx.conf` | 参考 Nginx 配置，约束 Session/CSRF 路径 |
| `docs/ARCHITECTURE.md` | 仍为占位，后续需补充最新架构图 |

## 5. 后端开发要点
- **主要依赖**：FastAPI、psutil、pam、rclpy（可选）。`backend/requirements.txt` 记录核心依赖。
- **路由说明**
  - `auth`：Linux PAM 登录、管理员校验、登出，依赖 CSRF Token 与内存限流。
  - `system`：`/status` 聚合 `SystemMonitor` 的 CPU/内存/磁盘/网络信息；`/power` 通过特权助手执行重启/关机。
  - `device`：返回主机名、架构、IP 等基础数据（含 openEuler 识别）。
  - `ros`：读取 `ROSMonitor` 的节点/话题/服务数量及稳定度。
- **服务层**
  - `SystemMonitor` 使用 psutil 采样并带 2 秒缓存。
  - `ROSMonitor` 在后台线程启动 rclpy 节点，若环境缺失则回退到默认值并记录警告。
  - `admin_privileged` 调用 `/usr/local/libexec/` 下的 helper 程序；无 helper 时会返回错误。
- **中间件/依赖**
  - `csrf_protection` 目前使用内存存储 Token，需要在登录页注入 `<meta>` 后通过前端同步。
  - `admin_auth` 用内存保存管理员 Session，默认 30 分钟；任务执行前需注意清理过期 Session。
  - `rate_limiter` 对登录/管理员验证进行 IP 级限流（默认 5 次/5 分钟）。
- **日志**
  - 运行脚本时日志位于 `/var/log/rosdeck/backend.log`。
  - FastAPI 也会在控制台输出基本访问日志（见 `app/main.py` 中间件）。

## 6. 前端开发要点
- **结构**：所有模块遵循 `index.html` + `main.js` + `style.css`。页面进入时 `index.js` 中的 `loadModule()` 会拉取模块并执行 `window.moduleInit`、离开时调用 `window.moduleCleanup`。
- **技术栈**：jQuery + ES 模块风格脚本，统一使用 4 空格缩进、camelCase 函数命名。
- **状态同步**：
  - `index.js` 负责全局状态栏、侧边栏、管理员模式切换、定时刷新 `/api/system/status`。
  - 概览模块示例展示了如何轮询 `/api/ros/stats` 并渲染数据。
  - CSRF Token 通过本地存储 `rosdeck_csrf_token` 维护，需要在提交敏感操作时写入 `X-CSRF-Token`。
- **模块现状**：
  - `overview` 已绑定 ROS 指标。
  - `network`、`storage`、`file-transfer` 等页面已有基础结构，但部分 JS 仍待实现（如 `terminal/main.js` 为空）。
  - 未来的“大模型交互”尚未落地，可在此基础上新增模块如 `modules/ai-console/`。

## 7. 特权助手与系统依赖
- `privileged/rosdeck_auth_helper.c`：通过 PAM 验证指定用户密码，run_dev.sh 会编译后安装到 `/usr/local/libexec/rosdeck-auth-helper` 并设置 setuid。
- `privileged/src/rosdeck_control_helper.c`：执行 `reboot` / `shutdown` 等命令，并同时创建到 `rosdeck-power-helper` 的兼容符号链接。
- 更新 C 源码后运行 `scripts/run_dev.sh` 会自动重新编译；务必审查安全性（输入校验、最小权限）。

## 8. 脚本与部署注意事项
- `scripts/run_dev.sh`
  - 会使用 sudo 创建目录、同步静态文件、覆盖 `/etc/nginx/conf.d/rosdeck.conf`、安装 helper。
  旁注：在生产/共享环境运行前先备份原有 Nginx 配置。
  - 后端进程以当前用户身份运行，PID 写入 `/run/rosdeck-backend.pid`。
  - 健康检查默认等待 ~7 秒；在慢设备上可能误报，可调大 `sleep` 与 `MAX_RETRY`。
- `scripts/stop.sh`
  - 根据 PID 文件与 `uvicorn` 进程特征终止后端，如端口仍占用会强制 `kill -9`。
  - 适合在跑集成测试前清理环境。
- `nginx/rosdeck.nginx.conf`
  - 登录页和部分 API 直接反代后端，其他静态资源从本地读取。
  - `/api/` 默认要求存在 `session_id` Cookie，可按需扩展鉴权逻辑。

## 9. 安全与合规
- 不要在仓库内提交生产配置与机密信息；自定义配置基于 `backend/config.example.yaml` 复制。
  - CSRF Token 目前内存保存，若要部署多实例需改为共享存储（Redis 等）。
  - 管理员 Session 也是内存态实现，后续可迁移到数据库或 Redis。
- 特权 helper 拥有 root 权限，一定要审查输入处理、日志输出与可执行路径。
- 健康检查和静态资源路径暴露在 LAN 中，部署时需额外加防火墙或 VPN。

## 10. 测试与质量保障
- 目前缺少自动化测试；新增功能时请在 `backend/tests/` 使用 pytest + FastAPI `TestClient` 编写覆盖。
  - 建议以路由维度命名（如 `test_system_status.py`）。
  - 对特权操作可 mock `subprocess.run`。
- 前端暂未有自动化测试，提交 PR 时至少提供手动测试清单（浏览器、步骤、期望结果）。
- 格式化：Python 遵循 PEP 8 / Black 风格；前端 CSS/JS 保持 4 空格缩进、双引号优先。

## 11. 调试与排错
- 查看后端日志：`tail -f /var/log/rosdeck/backend.log`
- 确认进程：`ps aux | grep uvicorn` / `ss -ltnp | grep 4162`
- 健康检查：`curl -fsS http://127.0.0.1:4162/api/health`
- ROS 指标缺失时，检查 `rclpy` 是否可导入，并确认 `ROS_DISTRO` 环境变量。
- 若前端无法加载，先验证 Nginx proxy 是否启用、静态资源是否同步到 `/usr/share/nginx/html/rosdeck`。

## 12. 工作流与协作规范
- 提交信息建议使用 `type(scope): subject`（如 `feat(system): add disk stats cache`），72 字符以内。
- 合并前确认：
  1. 代码通过自检并附带必要测试。
  2. 更新相关文档/注释。
  3. 如需 sudo、系统安装步骤，请在 PR 描述中注明风险。
- 建议在 `docs/ARCHITECTURE.md` 中同步结构演进，保持与本手册一致。

## 13. 近期路线图（根据当前愿景）
1. **监控完善**
   - 拓展系统监控：温度、功耗、磁盘分区明细。
   - 网络管理：实时带宽曲线、Wi-Fi/有线切换。
   - 文件传输、终端模块补齐前端逻辑并串联后端 API。
2. **ROS 深度集成**
   - 扩展 `ros_monitor`，提供话题带宽、节点资源占用等数据。
   - 支持 ROS 图谱可视化与基础操作（启动/停止节点）。
3. **大模型联动（探索阶段）**
   - 设计 `modules/ai-console/` 原型，实现消息输入、上下文展示。
   - 后端准备与 LLM 交互的调用骨架（可先 mock）。
   - 思考安全边界（模型发起操作需管理员确认等）。

## 14. 新接手者清单
1. 克隆仓库，阅读本手册和 `backend/app/main.py`、`html/index.js`。
2. 在测试环境中运行 `scripts/run_dev.sh`，验证前端/后端连通。
3. 熟悉 `privileged/` helper 的编译安装过程，确认所在环境允许 setuid。
4. 针对要开发的功能，定位对应模块（后端路由或前端模块）并补充缺失测试。
5. 记录手动验证步骤，更新文档或 README（必要时附截图）。

---

如需更多上下文或决策，请在 PR/Issue 中记录讨论，保持仓库内文档同步更新，便于后续模型快速接力。***
