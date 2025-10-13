# ROS-AI 指挥官 Turtlesim 集成设置指南

## 系统要求

- Ubuntu/openEuler Linux 系统
- ROS 2 (Humble/Foxy/Galactic)
- Python 3.8+
- X11 窗口系统

## 安装依赖

### 1. 更新 Python 依赖

```bash
cd /home/ye/openeuler/shared/RosDeck/backend
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 安装 ROS 2 Turtlesim

```bash
# 如果尚未安装
sudo apt install ros-${ROS_DISTRO}-turtlesim

# 或者在 openEuler 上
sudo yum install ros-${ROS_DISTRO}-turtlesim
```

### 3. 安装 FFmpeg（用于视频流推送）

```bash
# Debian/Ubuntu
sudo apt install ffmpeg

# openEuler / RHEL
sudo yum install ffmpeg
```

### 4. 验证 ROS 2 环境

```bash
source /opt/ros/${ROS_DISTRO}/setup.bash
ros2 pkg list | grep turtlesim
```

## 功能特性

### ✅ 已实现的功能

1. **自动启动 Turtlesim**
   - 前端检测 turtlesim 状态
   - 未运行时显示启动提示
   - 一键启动 turtlesim_node

2. **实时数据推送**
   - WebSocket 连接推送位姿数据
   - 位置 (x, y)
   - 角度 (theta)
   - 线速度和角速度

3. **实时视频流预览**
   - 自动定位 turtlesim 窗口并使用 FFmpeg 抓取画面
   - 前端通过 MJPEG 流展示实时图像
   - 支持断线自动重连与手动刷新按钮

3. **AI 自然语言控制**
   - 简单规则匹配（可扩展为 LLM）
   - 支持的指令：
     - "前进 N 米"
     - "后退 N 米"
     - "左转 N 度"
     - "右转 N 度"
     - "停止"
     - "画正方形/圆"

## 测试步骤

### 1. 启动后端服务

```bash
cd /home/ye/openeuler/shared/RosDeck/backend
source .venv/bin/activate

# 确保 ROS 2 环境已加载
source /opt/ros/${ROS_DISTRO}/setup.bash

# 启动 FastAPI 服务
uvicorn app.main:app --reload --host 127.0.0.1 --port 4162
```

### 2. 访问前端

打开浏览器访问：`http://localhost:1221`（或您配置的地址）

导航到：**ROS** → **AI 指挥官**

### 3. 启动 Turtlesim

- 页面会显示"Turtlesim 未启动"提示
- 点击"启动 Turtlesim"按钮
- 等待几秒，turtlesim 窗口会出现
- 前端界面激活，显示实时数据

### 4. 测试功能

#### 测试 AI 控制
1. 在对话框输入："前进 2 米"
2. 观察小乌龟移动
3. 右上角 JSON 区域显示命令
4. 右下角日志显示位置更新

#### 测试实时数据
1. 在终端手动控制小乌龟：
   ```bash
   ros2 run turtlesim turtle_teleop_key
   ```
2. 使用方向键移动小乌龟
3. 观察前端日志实时显示位置和速度

## 架构说明

### 后端服务架构

```
backend/app/
├── services/
│   └── turtlesim_manager.py       # Turtlesim 管理
│       ├── TurtlesimNode          # ROS 节点（订阅/发布）
│       ├── TurtlesimWindowCapture # 窗口捕获
│       └── TurtlesimManager       # 生命周期管理
├── ws/
│   └── turtlesim_ws.py            # WebSocket 端点
└── routes/
    └── ros_ai.py                  # REST API 路由
```

### 数据流

```
前端 → REST API → TurtlesimManager → ROS Topic
                                    ↓
前端 ← WebSocket ← Pose Callback ← /turtle1/pose
                                    ↑
前端 ← Video Stream ← Window Capture ← X11 Window
```

## 高级配置

### WebSocket 推送频率

由 ROS 话题频率决定，通常约 60 Hz

## 故障排查

### 问题：WebSocket 连接失败

**症状**：日志区域显示"WebSocket 连接错误"

**解决方案**：
1. 检查 session cookie 是否有效
2. 确认后端服务正在运行
3. 查看浏览器控制台错误信息

### 问题：Turtlesim 无法启动

**症状**：点击启动按钮后显示"启动失败"

**解决方案**：
1. 确保 ROS 2 环境已配置：
   ```bash
   echo $ROS_DISTRO
   ```
2. 手动测试 turtlesim：
   ```bash
   ros2 run turtlesim turtlesim_node
   ```
3. 检查后端日志：
   ```bash
   tail -f /var/log/rosdeck/backend.log
   ```

### 问题：AI 指令不响应

**症状**：输入指令后没有反应

**解决方案**：
1. 检查 turtlesim 是否正在运行
2. 查看浏览器控制台网络请求
3. 确认后端 `/api/ros/ai/command` 端点可访问

## 性能优化建议

1. **减少 WebSocket 推送频率**：
   - 在 `turtlesim_manager.py` 中添加节流逻辑

3. **启用 JPEG 压缩**：
   - 已默认启用，quality=80

## 扩展建议

### 集成真实 LLM API

编辑 `backend/app/routes/ros_ai.py` 的 `process_ai_command()` 函数：

```python
# 替换规则匹配为 LLM API 调用
import openai  # 或其他 LLM SDK

response = openai.ChatCompletion.create(
    model="gpt-4",
    messages=[
        {"role": "system", "content": "你是 ROS 控制助手..."},
        {"role": "user", "content": message}
    ]
)

# 解析 LLM 返回的 JSON 指令
command = json.loads(response.choices[0].message.content)
```

### 添加轨迹录制

可以在 WebSocket 回调中记录位姿历史，实现轨迹回放功能。

## 文件清单

- `backend/requirements.txt` - 更新的依赖列表
- `backend/app/services/turtlesim_manager.py` - Turtlesim 管理服务
- `backend/app/ws/turtlesim_ws.py` - WebSocket 端点
- `backend/app/routes/ros_ai.py` - REST API 路由
- `html/modules/ros/ai-commander/index.html` - 前端页面
- `html/modules/ros/ai-commander/main.js` - 前端逻辑
- `html/modules/ros/ai-commander/style.css` - 前端样式

## 贡献

欢迎提交 Issue 和 Pull Request！
