# RosDeck 全局审查报告

## 架构合理性
- **严重**: 会话体系缺少服务端状态，`/login` 仅把 `session_{username}_{timestamp}` 写入 Cookie 并依靠解析结果做鉴权，攻击者可伪造 Cookie 冒充任意用户；`get_current_username` 也只解析字符串，不做签名或存储校验。参见 `backend/app/routes/auth.py:102` 与 `backend/app/deps/admin_auth.py:146`。
- **高**: 安全控制均以内存单例实现（CSRF、管理员会话、限流器），多进程部署或服务重启会导致状态丢失或不一致，削弱安全效果。参见 `backend/app/deps/csrf.py:13` 与 `backend/app/deps/admin_auth.py:21`。
- **中**: 多个系统级操作直接在 FastAPI 事件循环内同步执行（`systemctl`, `time.sleep` 等），一旦调用阻塞会拖垮整个实例。参见 `backend/app/services/service_manager.py:28` 与 `backend/app/services/process_monitor.py:40`。
- **中**: 登录页从固定路径 `/usr/share/nginx/html/rosdeck/auth/login.html` 读取并写回 Token，部署环境变动时需要同时调整 Nginx 与应用，耦合度高。参见 `backend/app/main.py:111`。

## 模块解耦
- **高**: 路由直接耦合模块级单例（如 `ProcessMonitor`、`ServiceManager`），缺乏接口和依赖注入，阻碍测试与替换实现。参见 `backend/app/routes/runtime.py:10`。
- **中**: 终端通道把会话、命令历史、管理员状态分别保存在 `terminal_manager` 和 `admin_auth` 的全局字典里，缺少集中会话存储与并发保护。参见 `backend/app/routes/terminal.py:61` 与 `backend/app/services/terminal_manager.py:251`。
- **中**: ROS 相关服务在路由中跨模块互相调用（`ros_ai_planner`、`turtlesim_manager`、`turtlesim_capture`），异常处理和状态同步分散在路由与服务之间，建议抽象统一的 orchestrator。参见 `backend/app/routes/ros_ai.py:70` 与 `backend/app/services/turtlesim_manager.py:115`。

## 代码一致性
- **高**: CSRF 校验方式不一致，`system` 路由使用 FastAPI 依赖统一校验，而 `runtime` 路由改用手写函数读取全局 Token，容易遗漏或绕过。参见 `backend/app/routes/system.py:49` 与 `backend/app/routes/runtime.py:19`。
- **中**: 响应结构不统一，同一领域内有的返回 `{success: True}`，有的直接返回资源字典（如 `system/status`），前端处理需要额外分支。参见 `backend/app/routes/system.py:31` 与 `backend/app/routes/runtime.py:45`。
- **中**: 日志格式混用中文/英文、info/warning 混用，且缺少统一的日志上下文（用户、请求 ID 等），难以在集中日志中检索。例见 `backend/app/main.py:52` 与 `backend/app/routes/auth.py:90`。

## 错误处理与日志规范
- **严重**: `/api/metrics` 捕获所有异常而静默返回 `{ok: False}`，没有记录失败细节，实际错误无人知晓。参见 `backend/app/main.py:75`。
- **高**: `ProcessMonitor.get_processes` 捕获所有异常并返回空列表，调用方无法区分「无数据」与「采集失败」，导致监控失真。参见 `backend/app/services/process_monitor.py:81`。
- **中**: 多处同步子进程调用只记录 `stderr`，但未附带命令/参数上下文，定位失败时需要额外重现。例见 `backend/app/services/service_manager.py:115`。
- **中**: 终端 WebSocket 在读取/写入异常时只写 error 日志但不告知前端，用户感知为连接断开。参见 `backend/app/routes/terminal.py:120` 与 `backend/app/routes/terminal.py:184`。

## 可维护性与潜在技术债
- **严重**: 仓库缺少任何自动化测试目录或用例，涉及系统级命令和 ROS 控制，回归风险极高。
- **高**: `RosAI` 规划器内置默认 API Key，若进入仓库或发布包即泄漏凭据；应改为强制配置并移除默认值。参见 `backend/app/services/ros_ai_planner.py:22`。
- **高**: CSRF/管理员/限流状态全部驻留内存，缺少持久化或集中配置，不仅影响安全，也让多实例水平扩展变得不可行。参见 `backend/app/deps/csrf.py:13`、`backend/app/deps/admin_auth.py:21`、`backend/app/deps/rate_limit.py:8`。
- **中**: 大量同步子进程调用假设存在 `sudo`、`systemctl`、`ros2` 等命令，缺少运行前检测与错误提示，部署在裁剪系统时将频繁失败。参见 `backend/app/services/service_manager.py:104` 与 `backend/app/services/ros_launch_manager.py:127`。
- **中**: 前端依赖全局变量（如 `adminModeActive`）驱动子模块状态，缺少集中状态管理或事件契约，功能扩展时容易产生竞态。参见 `html/index.js:7` 与 `html/modules/runtime/main.js:15`。

## 主要建议
1. 重构认证/授权链路：引入服务端会话存储或 JWT，结合 per-user CSRF Token，补充签名与过期校验，消除可伪造 Cookie 风险。
2. 将安全相关状态（CSRF、管理员会话、限流）迁移到共享存储（Redis/数据库），并为多实例部署设计同步机制；同步梳理依赖注入，替换全局单例。
3. 把系统命令与 ROS 操作封装为后台任务或线程池调用，并在路由层使用 `run_in_threadpool` 等方式解耦事件循环，补充命令上下文与错误报告。
4. 制定统一的响应与日志规范（结构化日志，统一 `success/data/error` 模式），完善错误上报，并在前端统一处理。
5. 建立自动化测试基线：至少覆盖认证、关键系统操作的接口契约，必要时通过依赖注入模拟系统命令，保证后续 refactor 可验证。
