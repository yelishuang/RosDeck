(() => {
    const RECONNECT_DELAY_MS = 5000;
    const PING_INTERVAL_MS = 20000;
    const STORAGE_KEY_THEME = 'rosdeck_terminal_theme';
    const MAX_BUFFERED_INPUT = 4096;

    const THEMES = {
        dark: {
            background: '#111827',
            toolbar: '#1f2937',
            text: '#f3f4f6',
            border: '#374151',
            accent: '#38bdf8',
            statusConnected: '#4ade80',
            statusError: '#f87171',
            panelBg: '#1f2937',
            panelBorder: '#334155',
            fontSize: 14,
            selection: 'rgba(56, 189, 248, 0.35)'
        },
        light: {
            background: '#f8fafc',
            toolbar: '#e2e8f0',
            text: '#0f172a',
            border: '#cbd5f5',
            accent: '#2563eb',
            statusConnected: '#16a34a',
            statusError: '#dc2626',
            panelBg: '#f1f5f9',
            panelBorder: '#cbd5f5',
            fontSize: 14,
            selection: 'rgba(37, 99, 235, 0.25)'
        },
        dracula: {
            background: '#282a36',
            toolbar: '#343746',
            text: '#f8f8f2',
            border: '#44475a',
            accent: '#bd93f9',
            statusConnected: '#50fa7b',
            statusError: '#ff5555',
            panelBg: '#343746',
            panelBorder: '#44475a',
            fontSize: 14,
            selection: 'rgba(189, 147, 249, 0.35)'
        },
        monokai: {
            background: '#272822',
            toolbar: '#2d2e27',
            text: '#f8f8f2',
            border: '#3e3d32',
            accent: '#f9d342',
            statusConnected: '#a6e22e',
            statusError: '#f92672',
            panelBg: '#2d2e27',
            panelBorder: '#3e3d32',
            fontSize: 14,
            selection: 'rgba(249, 211, 66, 0.25)'
        }
    };

    const BASE_XTERM_THEME = {
        black: '#000000',
        red: '#f87171',
        green: '#34d399',
        yellow: '#facc15',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#22d3ee',
        white: '#e5e7eb',
        brightBlack: '#6b7280',
        brightRed: '#f87171',
        brightGreen: '#4ade80',
        brightYellow: '#facc15',
        brightBlue: '#38bdf8',
        brightMagenta: '#c084fc',
        brightCyan: '#22d3ee',
        brightWhite: '#f3f4f6'
    };

    const XTERM_THEME_OVERRIDES = {
        light: {
            black: '#1f2937',
            brightBlack: '#475569',
            white: '#0f172a',
            brightWhite: '#0f172a',
            background: '#f8fafc'
        }
    };

    const state = {
        moduleActive: false,
        socket: null,
        reconnectTimer: null,
        pingTimer: null,
        theme: 'dark',
        session: {
            username: '-',
            isAdmin: false,
            adminExpiresIn: 0
        },
        history: [],
        pendingInput: '',
        bufferWarned: false
    };

    const elements = {};

    let terminal = null;
    let terminalDisposables = [];
    let geometryTimer = null;
    let xtermAssetsPromise = null;

    function cacheDom() {
        elements.$container = $('.terminal-container');
        elements.$statusIndicator = elements.$container.find('.status-indicator');
        elements.$statusText = elements.$container.find('.status-text');
        elements.$usernameText = elements.$container.find('.username-text');
        elements.$display = $('#terminal-display');
        elements.$clearBtn = $('#btn-clear');
        elements.$historyBtn = $('#btn-history');
        elements.$themeBtn = $('#btn-theme');
        elements.$logBtn = $('#btn-log');
        elements.$historyPanel = $('#history-panel');
        elements.$historyList = $('#history-list');
        elements.$logPanel = $('#log-panel');
        elements.$logList = $('#log-list');
        elements.$themePanel = $('#theme-panel');
        elements.$themeOptions = elements.$themePanel.find('.theme-option');
        elements.$panelCloseButtons = $('.side-panel .btn-close-panel');
    }

    function bindEvents() {
        elements.$clearBtn.off('.terminal').on('click.terminal', () => clearTerminal(true));

        elements.$historyBtn.off('.terminal').on('click.terminal', () => {
            togglePanel(elements.$historyPanel);
            if (elements.$historyPanel.hasClass('open')) {
                void loadCommandHistory();
            }
        });

        elements.$logBtn.off('.terminal').on('click.terminal', () => togglePanel(elements.$logPanel));
        elements.$themeBtn.off('.terminal').on('click.terminal', () => togglePanel(elements.$themePanel));
        elements.$panelCloseButtons.off('.terminal').on('click.terminal', closePanels);
        elements.$display.off('.terminal').on('click.terminal', focusTerminal);

        elements.$themeOptions.off('.terminal').on('click.terminal', function() {
            const themeName = $(this).data('theme');
            if (themeName) {
                applyTheme(themeName);
            }
        });

        $(window).off('resize.terminal').on('resize.terminal', () => scheduleGeometrySync());
    }

    function unbindEvents() {
        elements.$clearBtn.off('.terminal');
        elements.$historyBtn.off('.terminal');
        elements.$themeBtn.off('.terminal');
        elements.$logBtn.off('.terminal');
        elements.$panelCloseButtons.off('.terminal');
        elements.$display.off('.terminal');
        elements.$themeOptions.off('.terminal');
        $(window).off('resize.terminal');
    }

    function ensureXtermAssets() {
        if (window.Terminal) {
            return Promise.resolve();
        }

        if (xtermAssetsPromise) {
            return xtermAssetsPromise;
        }

        xtermAssetsPromise = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = 'libs/xterm/xterm.min.js';
            script.async = true;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error('加载 xterm.js 失败'));
            document.head.appendChild(script);
        });

        return xtermAssetsPromise;
    }

    function createTerminal() {
        destroyTerminal();

        if (!window.Terminal || !elements.$display?.length) {
            return;
        }

        const theme = THEMES[state.theme] || THEMES.dark;

        terminal = new window.Terminal({
            cols: 80,
            rows: 24,
            convertEol: true,
            cursorBlink: true,
            fontFamily: 'Fira Code, Source Code Pro, Menlo, monospace',
            fontSize: theme.fontSize,
            rendererType: 'canvas',
            allowProposedApi: true,
            theme: buildXtermTheme(theme)
        });

        terminalDisposables.push(
            terminal.onData(data => {
                handleTerminalInput(data);
            })
        );

        terminalDisposables.push(
            terminal.onResize(size => {
                if (state.socket && state.socket.readyState === WebSocket.OPEN) {
                    sendMessage({ type: 'resize', cols: size.cols, rows: size.rows });
                }
            })
        );

        terminal.open(elements.$display.get(0));
        focusTerminal();
        scheduleGeometrySync(50);
        scheduleGeometrySync(250);
    }

    function destroyTerminal() {
        if (geometryTimer) {
            clearTimeout(geometryTimer);
            geometryTimer = null;
        }

        terminalDisposables.forEach(disposable => {
            try {
                disposable.dispose();
            } catch (_) {
                /* ignore */
            }
        });
        terminalDisposables = [];

        if (terminal) {
            try {
                terminal.dispose();
            } catch (_) {
                /* ignore */
            }
            terminal = null;
        }
    }

    function buildXtermTheme(theme) {
        const overrides = XTERM_THEME_OVERRIDES[state.theme] || {};
        return {
            ...BASE_XTERM_THEME,
            background: theme.background,
            foreground: theme.text,
            selectionBackground: theme.selection,
            cursor: theme.accent,
            cursorAccent: theme.text,
            black: overrides.black || BASE_XTERM_THEME.black,
            red: overrides.red || BASE_XTERM_THEME.red,
            green: overrides.green || BASE_XTERM_THEME.green,
            yellow: overrides.yellow || BASE_XTERM_THEME.yellow,
            blue: overrides.blue || BASE_XTERM_THEME.blue,
            magenta: overrides.magenta || BASE_XTERM_THEME.magenta,
            cyan: overrides.cyan || BASE_XTERM_THEME.cyan,
            white: overrides.white || BASE_XTERM_THEME.white,
            brightBlack: overrides.brightBlack || BASE_XTERM_THEME.brightBlack,
            brightRed: overrides.brightRed || BASE_XTERM_THEME.brightRed,
            brightGreen: overrides.brightGreen || BASE_XTERM_THEME.brightGreen,
            brightYellow: overrides.brightYellow || BASE_XTERM_THEME.brightYellow,
            brightBlue: overrides.brightBlue || BASE_XTERM_THEME.brightBlue,
            brightMagenta: overrides.brightMagenta || BASE_XTERM_THEME.brightMagenta,
            brightCyan: overrides.brightCyan || BASE_XTERM_THEME.brightCyan,
            brightWhite: overrides.brightWhite || BASE_XTERM_THEME.brightWhite
        };
    }

    function applyTheme(name, options = {}) {
        const nextTheme = THEMES[name] ? name : 'dark';
        state.theme = nextTheme;

        if (options.persist !== false) {
            try {
                window.localStorage.setItem(STORAGE_KEY_THEME, state.theme);
            } catch (_) {
                /* ignore */
            }
        }

        const theme = THEMES[state.theme];
        if (elements.$container?.length) {
            elements.$container.css({
                '--terminal-bg': theme.background,
                '--terminal-toolbar-bg': theme.toolbar,
                '--terminal-text': theme.text,
                '--terminal-border': theme.border,
                '--terminal-accent': theme.accent,
                '--terminal-status-connected': theme.statusConnected,
                '--terminal-status-error': theme.statusError,
                '--terminal-panel-bg': theme.panelBg,
                '--terminal-panel-border': theme.panelBorder,
                '--terminal-font-size': `${theme.fontSize}px`
            });
        }

        if (terminal) {
            terminal.setOption('theme', buildXtermTheme(theme));
            terminal.setOption('fontSize', theme.fontSize);
            scheduleGeometrySync(0);
        }

        if (elements.$themeOptions?.length) {
            elements.$themeOptions.removeClass('active');
            const $active = elements.$themeOptions.filter(`[data-theme="${state.theme}"]`);
            if ($active.length) {
                $active.addClass('active');
            }
        }
    }

    function restoreTheme() {
        let stored = null;
        try {
            stored = window.localStorage.getItem(STORAGE_KEY_THEME);
        } catch (_) {
            stored = null;
        }
        if (stored && THEMES[stored]) {
            applyTheme(stored, { persist: false });
        } else {
            applyTheme(state.theme, { persist: false });
        }
    }

    function updateStatus(status, message) {
        if (!elements.$statusIndicator?.length || !elements.$statusText?.length) {
            return;
        }

        elements.$statusIndicator.removeClass('connected error');

        switch (status) {
            case 'connecting':
                elements.$statusText.text('连接中...');
                break;
            case 'connected':
                elements.$statusIndicator.addClass('connected');
                elements.$statusText.text('已连接');
                break;
            case 'error':
                elements.$statusIndicator.addClass('error');
                elements.$statusText.text(message || '连接失败');
                break;
            case 'disconnected':
                elements.$statusText.text(message || '已断开');
                break;
            default:
                elements.$statusText.text('未连接');
        }
    }

    function updateUsernameDisplay() {
        if (!elements.$usernameText?.length) {
            return;
        }

        const name = state.session.username || '-';
        const suffix = state.session.isAdmin ? ' (管理员)' : '';
        elements.$usernameText.text(`${name}${suffix}`);
    }

    function focusTerminal() {
        if (terminal) {
            terminal.focus();
        }
    }

    function scheduleGeometrySync(delay = 0) {
        if (!terminal) {
            return;
        }
        if (geometryTimer) {
            clearTimeout(geometryTimer);
        }
        geometryTimer = window.setTimeout(() => {
            geometryTimer = null;
            syncTerminalGeometry();
        }, delay);
    }

    function syncTerminalGeometry() {
        if (!terminal || !elements.$display?.length) {
            return;
        }

        const container = elements.$display.get(0);
        if (!container) {
            return;
        }

        const core = terminal._core; // eslint-disable-line no-underscore-dangle
        const dims = core?._renderService?.dimensions; // eslint-disable-line no-underscore-dangle
        if (!dims || !dims.actualCellWidth || !dims.actualCellHeight) {
            terminal.refresh(0, terminal.rows - 1);
            return;
        }

        const availableWidth = container.clientWidth;
        const availableHeight = container.clientHeight;
        if (!availableWidth || !availableHeight) {
            return;
        }

        const cols = Math.max(40, Math.floor(availableWidth / dims.actualCellWidth) - 1);
        const rows = Math.max(16, Math.floor(availableHeight / dims.actualCellHeight) - 1);

        if (cols !== terminal.cols || rows !== terminal.rows) {
            terminal.resize(cols, rows);
        }
    }

    function appendOutput(text) {
        if (!terminal || !text) {
            return;
        }
        terminal.write(text);
    }

    function clearTerminal(showLog = false) {
        if (terminal) {
            terminal.clear();
            focusTerminal();
        }
        if (showLog) {
            addLog('info', '终端显示已清空');
        }
    }

    function addLog(level, message) {
        if (!elements.$logList?.length || !message) {
            return;
        }

        const timestamp = new Date().toLocaleTimeString();
        const $item = $('<div>').addClass('log-item').addClass(`log-${level}`);
        $item.append($('<div>').addClass('log-message').text(message));
        $item.append($('<div>').addClass('log-time').text(timestamp));

        elements.$logList.find('.empty-message').remove();
        elements.$logList.prepend($item);
    }

    function renderHistoryList(list) {
        if (!elements.$historyList?.length) {
            return;
        }

        elements.$historyList.empty();

        if (!Array.isArray(list) || list.length === 0) {
            elements.$historyList.append(
                $('<p>').addClass('empty-message').text('暂无命令历史')
            );
            return;
        }

        list.forEach(item => {
            const command = item.command || '';
            const time = item.timestamp
                ? new Date(item.timestamp).toLocaleString()
                : new Date().toLocaleString();

            const $historyItem = $('<div>').addClass('history-item');
            $historyItem.append($('<div>').addClass('command-text').text(command));
            $historyItem.append($('<div>').addClass('command-time').text(time));

            elements.$historyList.append($historyItem);
        });
    }

    function togglePanel($panel) {
        if (!$panel?.length) {
            return;
        }
        const shouldOpen = !$panel.hasClass('open');
        closePanels();
        if (shouldOpen) {
            $panel.addClass('open');
        }
    }

    function closePanels() {
        $('.side-panel').removeClass('open');
    }

    async function loadCommandHistory() {
        try {
            const response = await fetch('/api/terminal/history', {
                credentials: 'include'
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            if (data?.success && Array.isArray(data.history)) {
                state.history = data.history;
                renderHistoryList(state.history);
            } else {
                renderHistoryList([]);
            }
        } catch (error) {
            addLog('error', `获取命令历史失败: ${error.message}`);
            renderHistoryList(state.history);
        }
    }

    function sendMessage(payload) {
        if (state.socket && state.socket.readyState === WebSocket.OPEN) {
            try {
                state.socket.send(JSON.stringify(payload));
            } catch (error) {
                addLog('error', `发送数据失败: ${error.message}`);
            }
        }
    }

    function handleTerminalInput(data) {
        console.debug('[terminal] onData', JSON.stringify(data),
            state.socket?.readyState);

        if (!data) {
            return;
        }

        if (state.socket && state.socket.readyState === WebSocket.OPEN) {
            
            const payload = { type: 'input', data };
            console.debug('[terminal] send payload', payload);
            sendMessage(payload);

            return;
        }

        state.pendingInput += data;
        if (state.pendingInput.length > MAX_BUFFERED_INPUT) {
            state.pendingInput = state.pendingInput.slice(-MAX_BUFFERED_INPUT);
            if (!state.bufferWarned) {
                addLog('warning', '连接未建立，部分较早的输入已被丢弃');
                state.bufferWarned = true;
            }
        }
    }

    function flushInputBuffer() {
        if (!state.pendingInput || !(state.socket && state.socket.readyState === WebSocket.OPEN)) {
            return;
        }
        sendMessage({ type: 'input', data: state.pendingInput });
        state.pendingInput = '';
        state.bufferWarned = false;
    }

    function startPing() {
        stopPing();
        state.pingTimer = window.setInterval(() => {
            sendMessage({ type: 'ping' });
        }, PING_INTERVAL_MS);
    }

    function stopPing() {
        if (state.pingTimer) {
            window.clearInterval(state.pingTimer);
            state.pingTimer = null;
        }
    }

    function scheduleReconnect() {
        if (!state.moduleActive) {
            return;
        }
        if (state.reconnectTimer) {
            return;
        }
        state.reconnectTimer = window.setTimeout(() => {
            state.reconnectTimer = null;
            connectWebSocket();
        }, RECONNECT_DELAY_MS);
    }

    function cancelReconnect() {
        if (state.reconnectTimer) {
            window.clearTimeout(state.reconnectTimer);
            state.reconnectTimer = null;
        }
    }

    function handleSocketOpen() {
        updateStatus('connected');
        addLog('success', '终端连接已建立');
        stopPing();
        startPing();
        focusTerminal();
        flushInputBuffer();
        scheduleGeometrySync(0);
    }

    function handleSessionInfo(payload) {
        if (!payload) {
            return;
        }

        if (typeof payload.username === 'string') {
            state.session.username = payload.username;
        }
        if (typeof payload.is_admin === 'boolean') {
            state.session.isAdmin = payload.is_admin;
        }
        if (typeof payload.admin_session_expires_in === 'number') {
            state.session.adminExpiresIn = payload.admin_session_expires_in;
        }

        updateUsernameDisplay();

        if (typeof payload.is_admin === 'boolean') {
            addLog('info', payload.is_admin ? '管理员模式已启用' : '普通用户模式');
        }
    }

    function handleSocketMessage(event) {
        try {
            const payload = JSON.parse(event.data);
            switch (payload?.type) {
                case 'output':
                    appendOutput(payload.data || '');
                    break;
                case 'error':
                    appendOutput(`\r\n[错误] ${payload.message || payload.data || ''}\r\n`);
                    addLog('error', payload.message || '终端发生错误');
                    break;
                case 'command_blocked':
                    addLog('warning', `命令被拦截: ${payload.command || ''} (${payload.message || ''})`);
                    break;
                case 'command_allowed':
                    addLog('success', `命令已执行: ${payload.command || ''}`);
                    void loadCommandHistory();
                    break;
                case 'session_info':
                    handleSessionInfo(payload);
                    break;
                case 'pong':
                    break;
                default:
                    appendOutput(`\r\n[消息] ${JSON.stringify(payload)}\r\n`);
            }
        } catch (_) {
            appendOutput(event.data);
        }
    }

    function handleSocketError(event) {
        const message = event?.message || 'WebSocket 发生错误';
        updateStatus('error', '连接异常');
        addLog('error', message);
    }

    function handleSocketClose(event) {
        stopPing();
        const code = event.code || 1006;
        state.socket = null;

        if (!state.moduleActive) {
            updateStatus('disconnected', '已断开');
            return;
        }

        updateStatus('error', '连接断开，尝试重连');
        addLog('warning', `终端连接断开 (code: ${code})`);
        scheduleReconnect();
    }

    function connectWebSocket() {
        if (!state.moduleActive) {
            return;
        }

        if (state.socket && (state.socket.readyState === WebSocket.OPEN || state.socket.readyState === WebSocket.CONNECTING)) {
            return;
        }

        cancelReconnect();
        updateStatus('connecting');

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/api/terminal/ws`;

        try {
            state.socket = new WebSocket(url);
        } catch (error) {
            updateStatus('error', '无法创建连接');
            addLog('error', `创建 WebSocket 失败: ${error.message}`);
            scheduleReconnect();
            return;
        }

        state.socket.addEventListener('open', handleSocketOpen);
        state.socket.addEventListener('message', handleSocketMessage);
        state.socket.addEventListener('error', handleSocketError);
        state.socket.addEventListener('close', handleSocketClose);
    }

    async function fetchSessionInfo() {
        try {
            const response = await fetch('/api/terminal/session-info', {
                credentials: 'include'
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            if (!data?.success) {
                throw new Error(data?.message || '未知错误');
            }

            state.session.username = data.username || '-';
            state.session.isAdmin = !!data.is_admin;
            state.session.adminExpiresIn = data.admin_session_expires_in || 0;

            updateUsernameDisplay();
            addLog('info', '终端会话信息已同步');
            return true;
        } catch (error) {
            addLog('error', `会话信息获取失败: ${error.message}`);
            return false;
        }
    }

    async function initialiseModule() {
        updateStatus('connecting');

        try {
            await ensureXtermAssets();
        } catch (error) {
            updateStatus('error', '终端脚本加载失败');
            addLog('error', error.message);
            return;
        }

        createTerminal();
        clearTerminal(false);

        const ok = await fetchSessionInfo();
        if (!ok) {
            updateStatus('error', '会话无效');
            return;
        }

        connectWebSocket();
    }

    window.moduleInit = () => {
        state.moduleActive = true;
        cacheDom();
        bindEvents();
        restoreTheme();
        closePanels();
        focusTerminal();
        void initialiseModule();
    };

    window.moduleCleanup = () => {
        state.moduleActive = false;
        stopPing();
        cancelReconnect();

        if (state.socket) {
            try {
                state.socket.close(1001, 'module cleanup');
            } catch (_) {
                /* ignore */
            }
            state.socket = null;
        }

        unbindEvents();
        closePanels();
        destroyTerminal();
        state.pendingInput = '';
        state.bufferWarned = false;
        updateStatus('disconnected', '已断开');
    };
})();
