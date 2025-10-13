/**
 * ROS-AI 指挥官模块
 * 使用 AI 自然语言控制 ROS 小乌龟
 */

// ==================== 全局变量 ====================
let wsConnection = null;
let autoScroll = true;
let messageHistory = [];
let turtlesimRunning = false;
let videoStreamActive = false;
let videoRetryTimer = null;
let videoSessionToken = 0;
let videoHasConnected = false;
let lastLogSnapshot = null;
let jsonHighlightTimer = null;

// ==================== 模块初始化 ====================
window.moduleInit = function() {
    console.log('ROS-AI 指挥官模块初始化...');

    // 初始化事件监听
    initEventListeners();

    // 检查 turtlesim 状态并初始化
    checkTurtlesimStatus();
};

// ==================== 模块卸载 ====================
window.moduleCleanup = function() {
    console.log('ROS-AI 指挥官模块卸载...');

    // 清除定时器
    if (window.statusCheckInterval) {
        clearInterval(window.statusCheckInterval);
        window.statusCheckInterval = null;
    }

    // 关闭 WebSocket 连接
    if (wsConnection) {
        wsConnection.close();
        wsConnection = null;
    }

    if (jsonHighlightTimer) {
        clearTimeout(jsonHighlightTimer);
        jsonHighlightTimer = null;
    }

    stopVideoStream(true);

};

// ==================== Turtlesim 状态检查 ====================
function checkTurtlesimStatus() {
    fetch('/api/ros/turtlesim/status')
        .then(response => response.json())
        .then(data => {
            if (data.success && data.status.is_running) {
                // Turtlesim 正在运行
                turtlesimRunning = true;
                onTurtlesimReady();
            } else {
                // Turtlesim 未运行，显示启动提示
                turtlesimRunning = false;
                showTurtlesimStartPrompt();
                stopVideoStream(true);
            }
        })
        .catch(error => {
            console.error('检查 turtlesim 状态失败:', error);
            turtlesimRunning = false;
            showTurtlesimStartPrompt();
            stopVideoStream(true);
        });
}

function showTurtlesimStartPrompt() {
    setVideoPlaceholder('等待启动 turtlesim...', 'bi-rocket-takeoff');
    setVideoHint('点击下方按钮启动 turtlesim 后，将自动建立视频流。');
    updateVideoStatus('waiting', '待连接');

    if ($('#turtlesim-prompt').length) {
        $('#btn-start-turtlesim').show().prop('disabled', false);
        $('#prompt-status').hide();
    } else {
        // 在界面上显示启动提示
        $('.ai-commander-container').prepend(`
            <div class="turtlesim-prompt" id="turtlesim-prompt">
                <div class="prompt-card">
                    <div class="prompt-icon">
                        <i class="bi bi-robot"></i>
                    </div>
                    <h3 class="prompt-title">Turtlesim 未启动</h3>
                    <p class="prompt-message">请先启动 turtlesim 模拟器才能使用 AI 控制功能</p>
                    <button class="btn-start-turtlesim" id="btn-start-turtlesim">
                        <i class="bi bi-play-circle"></i>
                        <span>启动 Turtlesim</span>
                    </button>
                    <div class="prompt-status" id="prompt-status" style="display: none;">
                        <i class="bi bi-hourglass-split"></i>
                        <span>正在启动...</span>
                    </div>
                </div>
            </div>
        `);

        // 绑定启动按钮事件
        $('#btn-start-turtlesim').on('click', startTurtlesim);
    }

    // 在界面上显示启动提示
    // 禁用主要功能
    $('#btn-send').prop('disabled', true);
    $('#chat-input').prop('disabled', true);
}

function startTurtlesim() {
    const $btn = $('#btn-start-turtlesim');
    const $status = $('#prompt-status');

    // 显示状态
    $btn.hide();
    $status.show();
    $status.html('<i class="bi bi-hourglass-split"></i><span>正在启动进程...</span>');

    // 设置较长的超时时间（30秒），因为启动可能需要时间
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);

    fetch('/api/ros/turtlesim/start', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({}),
        signal: controller.signal
    })
    .then(async response => {
        clearTimeout(timeoutId);
        const rawText = await response.text();
        let payload = null;

        if (rawText && rawText.trim().length > 0) {
            try {
                payload = JSON.parse(rawText);
            } catch (parseError) {
                console.error('解析启动响应失败:', parseError, rawText);
                throw new Error(`启动响应异常 (HTTP ${response.status})`);
            }
        } else {
            payload = {};
        }

        if (!response.ok) {
            const detail = payload.detail || payload;
            const message = detail?.message || detail?.detail || `HTTP ${response.status}`;
            throw new Error(message);
        }

        return payload;
    })
    .then(data => {
        if (data.success) {
            // 启动成功
            $status.html('<i class="bi bi-check-circle"></i><span>启动成功！</span>');

            setTimeout(() => {
                $('#turtlesim-prompt').fadeOut(300, function() {
                    $(this).remove();
                });
                onTurtlesimReady();
            }, 1000);
        } else {
            throw new Error(data.message || '启动失败');
        }
    })
    .catch(error => {
        clearTimeout(timeoutId);
        console.error('启动 turtlesim 失败:', error);

        let errorMessage = error.message;
        if (error.name === 'AbortError') {
            errorMessage = '启动超时（30秒），请检查ROS环境配置';
        }

        stopVideoStream(true);
        updateVideoStatus('error', '启动失败');
        setVideoPlaceholder(`启动失败: ${errorMessage}`, 'bi-x-circle');
        setVideoHint('请检查 ROS 环境配置或从终端手动启动 turtlesim。');

        $status.html(`
            <i class="bi bi-x-circle"></i>
            <span>启动失败: ${errorMessage}</span>
        `);
        $status.css('color', '#ef4444');
        $btn.show();

        addLog('error', '启动 turtlesim 失败: ' + errorMessage);
    });
}

function onTurtlesimReady() {
    turtlesimRunning = true;

    // 启用功能
    $('#btn-send').prop('disabled', false);
    $('#chat-input').prop('disabled', false);

    // 初始化 WebSocket
    initWebSocket();

    // 检查连接状态
    checkConnectionStatus();

    // 定期检查连接状态
    window.statusCheckInterval = setInterval(checkConnectionStatus, 10000);

    updateVideoStatus('connecting', '视频连接中...');
    setVideoPlaceholder('正在连接 turtlesim 视频...', 'bi-hourglass-split');
    setVideoHint('若长时间无画面，可点击刷新按钮重新连接。');
    startVideoStream(true);

    addLog('success', 'Turtlesim 已就绪，可以开始控制了！');
}

// ==================== 事件监听初始化 ====================
function initEventListeners() {
    // 发送按钮
    $('#btn-send').on('click', sendMessage);

    // 输入框回车发送（Shift+Enter换行）
    $('#chat-input').on('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // 示例命令点击
    $(document).on('click', '.example-cmd', function() {
        const command = $(this).text().replace(/["""]/g, '');
        $('#chat-input').val(command);
        $('#chat-input').focus();
    });

    // 清空对话
    $('#btn-clear-chat').on('click', clearChat);

    // 复制 JSON
    $('#btn-copy-json').on('click', copyJson);

    // 日志控制
    $('#btn-toggle-autoscroll').on('click', toggleAutoScroll);
    $('#btn-clear-logs').on('click', clearLogs);

    // 视频刷新
    $('#btn-video-reload').on('click', function() {
        addLog('info', '手动发起视频重新连接');
        startVideoStream(true);
    });
}

// ==================== 检查连接状态 ====================
function checkConnectionStatus() {
    // 检查 AI 状态
    fetch('/api/ros/ai/status')
        .then(response => response.json())
        .then(data => {
            updateStatus('ai', data.connected ? 'connected' : 'error',
                        data.connected ? 'AI 就绪' : 'AI 离线');
        })
        .catch(() => {
            updateStatus('ai', 'error', 'AI 离线');
        });

    // 检查 ROS 状态
    fetch('/api/ros/turtlesim/status')
        .then(response => response.json())
        .then(data => {
            const connected = data.status && data.status.is_running;
            updateStatus('ros', connected ? 'connected' : 'error',
                        connected ? 'ROS 已连接' : 'ROS 离线');

            if (connected) {
                if (!turtlesimRunning) {
                    addLog('info', '检测到 turtlesim 已重新上线');
                }
                turtlesimRunning = true;
                startVideoStream();
            } else {
                if (turtlesimRunning) {
                    addLog('warning', '检测到 turtlesim 已停止');
                }
                turtlesimRunning = false;
                stopVideoStream(true);
                showTurtlesimStartPrompt();
            }
        })
        .catch(() => {
            updateStatus('ros', 'error', 'ROS 离线');
            turtlesimRunning = false;
            stopVideoStream(true);
            updateVideoStatus('error', '状态未知');
            setVideoPlaceholder('无法获取 turtlesim 状态，请稍后重试。', 'bi-wifi-off');
            setVideoHint('请检查后端服务是否启用，或刷新页面重新连接。');
        });
}

function updateStatus(type, state, text) {
    const $indicator = $(`#${type}-status`);
    $indicator.removeClass('connected connecting error');
    $indicator.addClass(state);
    $indicator.find('.status-text').text(text);
}

// ==================== AI 对话功能 ====================
function sendMessage() {
    const $input = $('#chat-input');
    const message = $input.val().trim();

    if (!message) return;

    const $chatPanel = $('.panel-chat');
    $chatPanel.addClass('is-busy');

    // 添加用户消息到界面
    addChatMessage('user', message);

    // 清空输入框
    $input.val('');

    // 禁用发送按钮
    $('#btn-send').prop('disabled', true);

    // 添加 AI 思考提示
    const thinkingId = addChatMessage('ai', '正在处理您的指令...', true);

    // 发送到后端 AI API
    fetch('/api/ros/ai/command', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            message: message,
            history: messageHistory.slice(-5) // 发送最近5条历史
        })
    })
    .then(response => response.json())
    .then(data => {
        // 移除思考提示
        $(`#${thinkingId}`).remove();

        // 添加 AI 回复
        addChatMessage('ai', data.reply || '指令已发送');

        // 更新 JSON 显示
        if (data.command) {
            updateJsonDisplay(data.command);
        }

        // 添加日志
        addLog('success', `AI 指令已执行: ${message.substring(0, 50)}...`);
    })
    .catch(error => {
        console.error('发送消息失败:', error);

        // 移除思考提示
        $(`#${thinkingId}`).remove();

        // 添加错误消息
        addChatMessage('ai', '抱歉，处理您的指令时出现错误。请稍后重试。');

        addLog('error', '发送指令失败: ' + error.message);
    })
    .finally(() => {
        // 启用发送按钮
        $('#btn-send').prop('disabled', false);
        $chatPanel.removeClass('is-busy');
    });
}

function addChatMessage(sender, content, isTemporary = false) {
    const $messages = $('#chat-messages');

    // 移除欢迎消息
    $('.chat-welcome').remove();

    const timestamp = new Date().toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
    });

    const messageId = `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

    const avatarIcon = sender === 'user' ? 'bi-person-fill' : 'bi-robot';
    const senderName = sender === 'user' ? '您' : 'AI 助手';

    const $message = $(`
        <div class="chat-message ${sender}" id="${messageId}">
            <div class="message-avatar ${sender}">
                <i class="bi ${avatarIcon}"></i>
            </div>
            <div class="message-content">
                <div class="message-header">
                    <span class="message-sender">${senderName}</span>
                    <span class="message-time">${timestamp}</span>
                </div>
                <div class="message-bubble">
                    ${content}
                </div>
            </div>
        </div>
    `);

    $messages.append($message);

    // 滚动到底部
    $messages.scrollTop($messages[0].scrollHeight);

    // 保存到历史记录（非临时消息）
    if (!isTemporary) {
        messageHistory.push({
            role: sender === 'user' ? 'user' : 'assistant',
            content: content,
            timestamp: new Date().toISOString()
        });
    }

    return messageId;
}

function clearChat() {
    const $messages = $('#chat-messages');
    $messages.empty();
    $messages.append(`
        <div class="chat-welcome">
            <i class="bi bi-lightbulb"></i>
            <p>对话已清空。继续输入指令控制小乌龟吧！</p>
        </div>
    `);
    messageHistory = [];
}

// ==================== JSON 显示功能 ====================
function updateJsonDisplay(data) {
    const $display = $('#json-display code');

    try {
        const formatted = JSON.stringify(data, null, 2);
        $display.text(formatted);

        // 高亮显示
        highlightJson($display);

        const $panel = $('.panel-json');
        if ($panel.length) {
            $panel.addClass('is-updated');
            if (jsonHighlightTimer) {
                clearTimeout(jsonHighlightTimer);
            }
            jsonHighlightTimer = setTimeout(() => {
                $panel.removeClass('is-updated');
                jsonHighlightTimer = null;
            }, 1200);
        }
    } catch (error) {
        console.error('格式化 JSON 失败:', error);
        $display.text(JSON.stringify(data));
    }
}

function highlightJson($element) {
    // 简单的 JSON 语法高亮
    let html = $element.text();

    // 高亮键名
    html = html.replace(/"([^"]+)":/g, '<span style="color: #60a5fa;">"$1"</span>:');

    // 高亮字符串值
    html = html.replace(/: "([^"]*)"/g, ': <span style="color: #34d399;">"$1"</span>');

    // 高亮数字
    html = html.replace(/: (\d+\.?\d*)/g, ': <span style="color: #f472b6;">$1</span>');

    // 高亮布尔值
    html = html.replace(/: (true|false)/g, ': <span style="color: #fbbf24;">$1</span>');

    $element.html(html);
}

function copyJson() {
    const text = $('#json-display code').text();

    navigator.clipboard.writeText(text)
        .then(() => {
            // 显示复制成功提示
            const $btn = $('#btn-copy-json');
            const originalHtml = $btn.html();
            $btn.html('<i class="bi bi-check"></i>');

            setTimeout(() => {
                $btn.html(originalHtml);
            }, 2000);

            addLog('info', 'JSON 已复制到剪贴板');
        })
        .catch(err => {
            console.error('复制失败:', err);
            addLog('error', '复制 JSON 失败');
        });
}

function scrollLogsToBottom() {
    const $logs = $('#log-messages');
    if (!$logs.length) return;
    const element = $logs[0];
    if (!element) return;
    element.scrollTop = element.scrollHeight - element.clientHeight;
}

// ==================== 日志功能 ====================
function addLog(type, message, data = null, options = {}) {
    const $logs = $('#log-messages');
    const { flashOnDuplicate = true } = options;

    const iconMap = {
        info: 'bi-info-circle',
        success: 'bi-check-circle',
        warning: 'bi-exclamation-triangle',
        error: 'bi-x-circle'
    };

    const icon = iconMap[type] || iconMap.info;

    let content = message;
    if (data) {
        content += `\n${JSON.stringify(data, null, 2)}`;
    }

    const entryKey = `${type}::${message}`;
    if (lastLogSnapshot && lastLogSnapshot.key === entryKey && lastLogSnapshot.$el?.length) {
        if (flashOnDuplicate) {
            flashLogEntry(lastLogSnapshot.$el);
        }
        return;
    }

    const timestamp = new Date().toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });

    const $entry = $(`
        <div class="log-entry log-${type}">
            <span class="log-time">${timestamp}</span>
            <span class="log-icon"><i class="bi ${icon}"></i></span>
            <span class="log-content">${content}</span>
        </div>
    `);

    $logs.append($entry);
    lastLogSnapshot = { key: entryKey, $el: $entry };

    // 限制日志条数（最多 500 条）
    const maxLogs = 500;
    const $entries = $logs.find('.log-entry');
    if ($entries.length > maxLogs) {
        $entries.slice(0, $entries.length - maxLogs).remove();
    }

    // 自动滚动
    if (autoScroll) {
        requestAnimationFrame(scrollLogsToBottom);
    }
}

function flashLogEntry($entry) {
    if (!$entry || !$entry.length) return;
    $entry.removeClass('log-flash');
    // 触发重绘以重置动画
    // eslint-disable-next-line no-unused-expressions
    $entry[0].offsetHeight;
    $entry.addClass('log-flash');
    setTimeout(() => {
        $entry.removeClass('log-flash');
    }, 600);
}

function toggleAutoScroll() {
    autoScroll = !autoScroll;
    const $btn = $('#btn-toggle-autoscroll');
    $btn.attr('data-active', autoScroll);

    if (autoScroll) {
        requestAnimationFrame(scrollLogsToBottom);
    }
}

function clearLogs() {
    const $logs = $('#log-messages');
    $logs.empty();
    $logs.append(`
        <div class="log-entry log-info">
            <span class="log-time">--:--:--</span>
            <span class="log-icon"><i class="bi bi-info-circle"></i></span>
            <span class="log-content">日志已清空</span>
        </div>
    `);
    lastLogSnapshot = null;
    if (autoScroll) {
        requestAnimationFrame(scrollLogsToBottom);
    }
}

// ==================== 视频流 ====================
function updateVideoStatus(state, text) {
    const $status = $('#video-status');
    if (!$status.length) return;

    $status.removeClass('waiting connecting connected error').addClass(state);
    if (text) {
        $status.find('.status-text').text(text);
    }

    const $panel = $('.panel-video');
    if ($panel.length) {
        $panel.removeClass('is-waiting is-connecting is-connected is-error');
        if (state) {
            $panel.addClass(`is-${state}`);
        }
    }
}

function setVideoPlaceholder(message, iconClass = 'bi-rocket-takeoff') {
    const $placeholder = $('#video-placeholder');
    if (!$placeholder.length) return;

    $placeholder.show();
    const $icon = $placeholder.find('i');
    if ($icon.length) {
        $icon.attr('class', `bi ${iconClass}`);
    }
    $placeholder.find('p').text(message || '');
}

function setVideoHint(message) {
    const $hint = $('#video-hint');
    if ($hint.length) {
        $hint.text(message || '');
    }
}

function scheduleVideoRetry(delay = 5000) {
    if (videoRetryTimer) {
        clearTimeout(videoRetryTimer);
    }
    videoRetryTimer = setTimeout(() => {
        videoRetryTimer = null;
        if (turtlesimRunning) {
            startVideoStream(true);
        }
    }, delay);
}

function handleVideoError(message) {
    console.error('视频流错误:', message);
    videoStreamActive = false;
    updateVideoStatus('error', message);
    setVideoPlaceholder(`${message}，稍后将自动重试。`, 'bi-exclamation-triangle');
    setVideoHint('请确认 turtlesim 窗口已打开且未被最小化。');
    scheduleVideoRetry();
}

function stopVideoStream(resetUI = false) {
    const $img = $('#turtle-video');
    if (!$img.length) return;

    videoStreamActive = false;
    videoSessionToken += 1;
    videoHasConnected = false;

    if (videoRetryTimer) {
        clearTimeout(videoRetryTimer);
        videoRetryTimer = null;
    }

    $img.off('.video');
    $img.removeClass('is-active');
    $img.attr('src', '');

    if (resetUI) {
        updateVideoStatus('waiting', '待连接');
        setVideoPlaceholder('等待启动 turtlesim...', 'bi-rocket-takeoff');
        setVideoHint('启动 turtlesim 后将自动开启视频流。');
    }
}

function startVideoStream(forceReload = false) {
    const $img = $('#turtle-video');
    if (!$img.length) return;

    if (!turtlesimRunning) {
        stopVideoStream(true);
        return;
    }

    if (videoStreamActive && !forceReload) {
        return;
    }

    if (videoRetryTimer) {
        clearTimeout(videoRetryTimer);
        videoRetryTimer = null;
    }

    videoStreamActive = true;
    videoSessionToken += 1;
    const currentToken = videoSessionToken;

    updateVideoStatus('connecting', '视频连接中...');
    setVideoPlaceholder('正在连接 turtlesim 视频...', 'bi-hourglass-split');
    setVideoHint('连接成功后将显示实时画面。');

    $img.off('.video');
    $img.removeClass('is-active');

    videoHasConnected = false;

    const streamUrl = `/api/ros/turtlesim/stream?ts=${Date.now()}`;
    addLog('info', `尝试连接视频流`, { url: streamUrl });
    console.log('[ROS-AI] startVideoStream ->', streamUrl, 'forceReload=', forceReload);

    $img.on('load.video', () => {
        if (videoSessionToken !== currentToken) {
            return;
        }
        $('#video-placeholder').hide();
        $img.addClass('is-active');
        updateVideoStatus('connected', '视频已连接');
        setVideoHint('若画面卡顿，可点击刷新按钮重新连接。');
        if (!videoHasConnected) {
            videoHasConnected = true;
            addLog('success', '视频流连接成功', null, { flashOnDuplicate: false });
        }
    });

    $img.on('error.video', () => {
        if (videoSessionToken !== currentToken) {
            return;
        }
        handleVideoError('视频加载失败');
        videoHasConnected = false;
    });

    $img.attr('src', streamUrl);
}

// ==================== WebSocket 连接 ====================
function initWebSocket() {
    // 构建 WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ros/ws/turtle`;

    try {
        wsConnection = new WebSocket(wsUrl);

        wsConnection.onopen = function() {
            console.log('WebSocket 连接已建立');
            addLog('success', 'WebSocket 连接已建立');
        };

        wsConnection.onmessage = function(event) {
            try {
                const data = JSON.parse(event.data);
                handleWebSocketMessage(data);
            } catch (error) {
                console.error('解析 WebSocket 消息失败:', error);
            }
        };

        wsConnection.onerror = function(error) {
            console.error('WebSocket 错误:', error);
            addLog('error', 'WebSocket 连接错误');
        };

        wsConnection.onclose = function() {
            console.log('WebSocket 连接已关闭');
            addLog('warning', 'WebSocket 连接已关闭');

            // 5 秒后尝试重连
            if (turtlesimRunning) {
                setTimeout(() => {
                    initWebSocket();
                }, 5000);
            }
        };
    } catch (error) {
        console.error('创建 WebSocket 连接失败:', error);
        addLog('error', '创建 WebSocket 连接失败: ' + error.message);
    }
}

function handleWebSocketMessage(data) {
    // 根据消息类型处理
    switch (data.type) {
        case 'pose':
            // 小乌龟位姿数据 - 后端已做变化检测，前端直接显示
            addLog('info', `位置: (${data.x?.toFixed(2)}, ${data.y?.toFixed(2)}), 角度: ${data.theta?.toFixed(2)}rad, ` +
                          `速度: ${data.linear_velocity?.toFixed(2)} m/s`);
            break;

        case 'velocity':
            // 速度数据 - 只在非零时显示
            if (Math.abs(data.linear) > 0.01 || Math.abs(data.angular) > 0.01) {
                addLog('info', `线速度: ${data.linear?.toFixed(2)} m/s, 角速度: ${data.angular?.toFixed(2)} rad/s`);
            }
            break;

        case 'status':
            // 状态更新
            addLog('success', data.message);
            break;

        case 'error':
            // 错误消息
            addLog('error', data.message);
            break;

        case 'ping':
            // 心跳消息，静默处理
            break;

        default:
            // 其他消息
            console.log('收到 WebSocket 消息:', data);
    }
}

// ==================== 工具函数 ====================
function formatTimestamp(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// ==================== 初始化完成 ====================
console.log('ROS-AI 指挥官模块脚本加载完成');
