/**
 * ROS AI Commander module.
 * Exposes natural-language turtlesim control via the backend AI bridge.
 */

// ==================== Module state ====================
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

// ==================== Module bootstrap ====================
window.moduleInit = function() {
    console.log('ROS-AI 指挥官模块初始化...');

    // Register DOM event listeners
    initEventListeners();

    // Probe turtlesim availability before enabling the UI
    checkTurtlesimStatus();
};

// ==================== Module teardown ====================
window.moduleCleanup = function() {
    console.log('ROS-AI 指挥官模块卸载...');

    // Clear polling timers to avoid leaks
    if (window.statusCheckInterval) {
        clearInterval(window.statusCheckInterval);
        window.statusCheckInterval = null;
    }

    // Close any active WebSocket connection
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

// ==================== Turtlesim status checks ====================
function checkTurtlesimStatus() {
    fetch('/api/ros/turtlesim/status')
        .then(response => response.json())
        .then(data => {
            if (data.success && data.status.is_running) {
                // Turtlesim is already running
                turtlesimRunning = true;
                onTurtlesimReady();
            } else {
                // Turtlesim is stopped; show the startup prompt
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
        // Inject the startup prompt card into the layout
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

        // Attach click handler for the launch action
        $('#btn-start-turtlesim').on('click', startTurtlesim);
    }

    // Ensure the prompt is visible and suspend primary controls
    $('#btn-send').prop('disabled', true);
    $('#chat-input').prop('disabled', true);
}

function startTurtlesim() {
    const $btn = $('#btn-start-turtlesim');
    const $status = $('#prompt-status');

    // Update the prompt to reflect the current action
    $btn.hide();
    $status.show();
    $status.html('<i class="bi bi-hourglass-split"></i><span>正在启动进程...</span>');

    // Allow up to 30 seconds for turtlesim to start before aborting
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
            // Startup succeeded
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

    // Re-enable conversation controls
    $('#btn-send').prop('disabled', false);
    $('#chat-input').prop('disabled', false);

    // Start the WebSocket bridge for telemetry
    initWebSocket();

    // Refresh status indicators immediately
    checkConnectionStatus();

    // Schedule periodic status checks
    window.statusCheckInterval = setInterval(checkConnectionStatus, 10000);

    updateVideoStatus('connecting', '视频连接中...');
    setVideoPlaceholder('正在连接 turtlesim 视频...', 'bi-hourglass-split');
    setVideoHint('若长时间无画面，可点击刷新按钮重新连接。');
    startVideoStream(true);

    addLog('success', 'Turtlesim 已就绪，可以开始控制了！');
}

// ==================== Event wiring ====================
function initEventListeners() {
    // Send button click
    $('#btn-send').on('click', sendMessage);

    // Submit on Enter; keep Shift+Enter for newlines
    $('#chat-input').on('keydown', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Sample command shortcuts
    $(document).on('click', '.example-cmd', function() {
        const command = $(this).text().replace(/["""]/g, '');
        $('#chat-input').val(command);
        $('#chat-input').focus();
    });

    // Conversation reset
    $('#btn-clear-chat').on('click', clearChat);

    // Copy JSON payload
    $('#btn-copy-json').on('click', copyJson);

    // Log panel controls
    $('#btn-toggle-autoscroll').on('click', toggleAutoScroll);
    $('#btn-clear-logs').on('click', clearLogs);

    // Manual video reconnect
    $('#btn-video-reload').on('click', function() {
        addLog('info', '手动发起视频重新连接');
        startVideoStream(true);
    });
}

// ==================== Status polling ====================
function checkConnectionStatus() {
    // Query AI backend status
    fetch('/api/ros/ai/status')
        .then(response => response.json())
        .then(data => {
            updateStatus('ai', data.connected ? 'connected' : 'error',
                        data.connected ? 'AI 就绪' : 'AI 离线');
        })
        .catch(() => {
            updateStatus('ai', 'error', 'AI 离线');
        });

    // Probe turtlesim process state
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

// ==================== AI conversation workflow ====================
function sendMessage() {
    const $input = $('#chat-input');
    const message = $input.val().trim();

    if (!message) return;

    const $chatPanel = $('.panel-chat');
    $chatPanel.addClass('is-busy');

    // Render the user message in the chat panel
    addChatMessage('user', message);

    // Reset the input field
    $input.val('');

    // Prevent duplicate submissions
    $('#btn-send').prop('disabled', true);

    // Show a temporary AI thinking indicator
    const thinkingId = addChatMessage('ai', '正在处理您的指令...', true);

    // Dispatch the command to the backend AI endpoint
    fetch('/api/ros/ai/command', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            message: message,
            history: messageHistory.slice(-5) // include the most recent five exchanges
        })
    })
    .then(response => response.json())
    .then(data => {
        // Remove the temporary placeholder
        $(`#${thinkingId}`).remove();

        // Display the AI response
        addChatMessage('ai', data.reply || '指令已发送');

        // Update the JSON preview pane
        if (data.command) {
            updateJsonDisplay(data.command);
        }

        // Write a success log entry
        addLog('success', `AI 指令已执行: ${message.substring(0, 50)}...`);
    })
    .catch(error => {
        console.error('发送消息失败:', error);

        // Remove the temporary placeholder
        $(`#${thinkingId}`).remove();

        // Show an error message in the chat
        addChatMessage('ai', '抱歉，处理您的指令时出现错误。请稍后重试。');

        addLog('error', '发送指令失败: ' + error.message);
    })
    .finally(() => {
        // Restore the send controls
        $('#btn-send').prop('disabled', false);
        $chatPanel.removeClass('is-busy');
    });
}

function addChatMessage(sender, content, isTemporary = false) {
    const $messages = $('#chat-messages');

    // Drop the welcome prompt once conversation begins
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

    // Ensure the latest message is visible
    $messages.scrollTop($messages[0].scrollHeight);

    // Persist permanent messages for conversation context
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

// ==================== JSON display helpers ====================
function updateJsonDisplay(data) {
    const $display = $('#json-display code');

    try {
        const formatted = JSON.stringify(data, null, 2);
        $display.text(formatted);

        // Apply a short-lived highlight effect
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
    // Lightweight JSON syntax highlighting
    let html = $element.text();

    // Highlight property names
    html = html.replace(/"([^"]+)":/g, '<span style="color: #60a5fa;">"$1"</span>:');

    // Highlight string values
    html = html.replace(/: "([^"]*)"/g, ': <span style="color: #34d399;">"$1"</span>');

    // Highlight numeric literals
    html = html.replace(/: (\d+\.?\d*)/g, ': <span style="color: #f472b6;">$1</span>');

    // Highlight boolean literals
    html = html.replace(/: (true|false)/g, ': <span style="color: #fbbf24;">$1</span>');

    $element.html(html);
}

function copyJson() {
    const text = $('#json-display code').text();

    navigator.clipboard.writeText(text)
        .then(() => {
            // Briefly swap the icon to acknowledge success
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

// ==================== Logging ====================
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

    // Keep at most 500 entries in the log buffer
    const maxLogs = 500;
    const $entries = $logs.find('.log-entry');
    if ($entries.length > maxLogs) {
        $entries.slice(0, $entries.length - maxLogs).remove();
    }

    // Auto-scroll when enabled by the user
    if (autoScroll) {
        requestAnimationFrame(scrollLogsToBottom);
    }
}

function flashLogEntry($entry) {
    if (!$entry || !$entry.length) return;
    $entry.removeClass('log-flash');
    // Force a reflow so the CSS animation restarts
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

// ==================== Video stream management ====================
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

// ==================== WebSocket bridge ====================
function initWebSocket() {
    // Build the WebSocket URL based on current protocol
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

            // Attempt reconnection five seconds after an unexpected close
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
    // Route messages according to their declared type
    switch (data.type) {
        case 'pose':
            // Turtlesim pose changes; backend already filters duplicates
            addLog('info', `位置: (${data.x?.toFixed(2)}, ${data.y?.toFixed(2)}), 角度: ${data.theta?.toFixed(2)}rad, ` +
                          `速度: ${data.linear_velocity?.toFixed(2)} m/s`);
            break;

        case 'velocity':
            // Velocity updates; ignore near-zero noise
            if (Math.abs(data.linear) > 0.01 || Math.abs(data.angular) > 0.01) {
                addLog('info', `线速度: ${data.linear?.toFixed(2)} m/s, 角速度: ${data.angular?.toFixed(2)} rad/s`);
            }
            break;

        case 'status':
            // Informational status message
            addLog('success', data.message);
            break;

        case 'error':
            // Error notification from backend
            addLog('error', data.message);
            break;

        case 'ping':
            // Heartbeat messages are ignored
            break;

        default:
            // Fallback handling for unexpected payloads
            console.log('收到 WebSocket 消息:', data);
    }
}

// ==================== Utility helpers ====================
function formatTimestamp(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// ==================== Bootstrap complete ====================
console.log('ROS-AI 指挥官模块脚本加载完成');
