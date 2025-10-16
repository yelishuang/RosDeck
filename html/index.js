/**
 * Core UI controller for the RosDeck dashboard.
 * Manages module navigation, user interactions, and periodic status refreshes.
 */

// Global state shared across modules.
let currentModule = 'overview';
let adminModeActive = false;
let adminModePending = false;
let statusUpdateInterval = null;
let deviceInfoInterval = null;
let powerActionPending = false;

const DEVICE_INFO_REFRESH_MS = 60000;
const API_VERIFY_ADMIN = '/api/auth/verify-admin';
const API_ADMIN_LOGOUT = '/api/auth/admin-logout';
const STORAGE_KEYS = {
    csrf: 'rosdeck_csrf_token'
};

// Bootstrap the dashboard when the DOM is ready.
$(document).ready(function() {
    console.log('RosDeck 初始化...');

    // Ensure the CSRF token is carried over from the login flow.
    ensureCsrfToken().catch(err => {
        console.error('初始化 CSRF Token 失败:', err);
    });
    
    // Initialize shared UI shells.
    initSidebar();
    initTopBar();
    initStatusRibbon();
    initContentArea();
    
    // Default to the overview module.
    loadModule('overview');
    
    // Start periodic refresh tasks.
    startStatusUpdate();
    startDeviceInfoUpdates();
});

// Sidebar navigation and power controls.
function initSidebar() {
    // Register handlers for module navigation.
    $('.submenu-item').on('click', function() {
        const modulePath = $(this).data('module');
        if (modulePath) {
            loadModule(modulePath);
            
            // Reflect selection in the sidebar.
            $('.submenu-item').removeClass('active');
            $(this).addClass('active');
        }
    });
    
    // Manage hover behavior for the power dropdown.
    let powerMenuTimeout;
    
    $('.power-btn').on('mouseenter', function() {
        clearTimeout(powerMenuTimeout);
        $('.power-dropdown').stop().fadeIn(200);
    });
    
    $('.power-btn').on('mouseleave', function() {
        powerMenuTimeout = setTimeout(function() {
            $('.power-dropdown').stop().fadeOut(200);
        }, 300); // Keep the menu open briefly to allow cursor travel.
    });
    
    // Trigger restart or shutdown actions for the selected option.
    $('.power-option').on('click', function(e) {
        e.stopPropagation();
        const action = $(this).data('action');
        $('.power-dropdown').fadeOut(200);
        handlePowerAction(action);
    });
    
    // Placeholder hook for editing the device identifier.
    $('.btn-edit').on('click', function() {
        if ($(this).data('editable') === 'true') {
            // TODO: Implement device identifier editing.
            console.log('设备ID编辑功能待实现');
        }
    });
}

// Top bar notifications and admin toggle.
function initTopBar() {
    // Placeholder wiring for notifications.
    $('.notification-btn').on('click', function() {
        // TODO: Render the notification panel.
        console.log('通知功能待实现');
        toastr.info('通知功能开发中...', '提示');
    });
    
    // Placeholder for user account menu.
    $('.user-card').on('click', function() {
        // TODO: Implement the user account dropdown.
        console.log('用户菜单待实现');
    });
    
    // Toggle admin mode state.
    $('.admin-mode-toggle').on('click', function() {
        toggleAdminMode();
    });
}

// Status ribbon setup.
function initStatusRibbon() {
    // Initialization is passive; recurring updates run elsewhere.
    console.log('状态栏初始化完成');
}

// Begin periodic system status polling.
function startStatusUpdate() {
    // Perform an immediate update.
    updateSystemStatus();
    
    // Schedule five-second polling.
    statusUpdateInterval = setInterval(updateSystemStatus, 5000);
}

// Refresh dashboard metrics.
function updateSystemStatus() {
    fetch('/api/system/status')
        .then(response => response.json())
        .then(data => {
            // Update uptime display.
            const uptimeElement = $('#uptime-value');
            if (uptimeElement.length && data.uptime_seconds) {
                const uptime = formatUptime(data.uptime_seconds);
                uptimeElement.text(uptime);
            }
            
            // Update disk usage display.
            const diskElement = $('#disk-value');
            if (diskElement.length && data.disk) {
                diskElement.text(data.disk.usage_percent + '%');
            }
            
            // Update memory usage display.
            const memoryElement = $('#memory-value');
            if (memoryElement.length && data.memory) {
                memoryElement.text(data.memory.usage_percent + '%');
            }
            
            // Update CPU usage display.
            const cpuElement = $('#cpu-value');
            if (cpuElement.length && data.cpu) {
                cpuElement.text(data.cpu.usage_percent + '%');
            }
            
            // Update network throughput display.
            const networkElement = $('#network-value');
            if (networkElement.length && data.network) {
                networkElement.text(data.network.speed_mbps.toFixed(1) + ' Mbps');
            }
        })
        .catch(error => console.error('获取系统状态失败:', error));
}

function formatUptime(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    
    if (days > 0) {
        return `${days}天 ${hours}小时`;
    } else if (hours > 0) {
        return `${hours}小时 ${minutes}分钟`;
    } else {
        return `${minutes}分钟`;
    }
}

// Device information polling.
function startDeviceInfoUpdates() {
    if (deviceInfoInterval) {
        clearInterval(deviceInfoInterval);
        deviceInfoInterval = null;
    }
    
    updateDeviceInfo();
    deviceInfoInterval = setInterval(updateDeviceInfo, DEVICE_INFO_REFRESH_MS);
}

function updateDeviceInfo() {
    fetch('/api/device/info')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            renderDeviceInfo(data);
        })
        .catch(error => {
            console.error('获取设备信息失败:', error);
            renderDeviceInfo();
        });
}

function renderDeviceInfo(raw = {}) {
    const defaults = {
        hostname: 'Unknown',
        status: 'offline',
        os: 'Unknown',
        architecture: 'Unknown',
        ip_address: '0.0.0.0'
    };
    
    const data = Object.assign({}, defaults, raw || {});
    const deviceCard = $('.device-card');
    
    if (!deviceCard.length) {
        return;
    }
    
    deviceCard.find('.device-name').text(data.hostname || defaults.hostname);
    
    const status = String(data.status || defaults.status).toLowerCase();
    const statusLabel = status === 'online' ? '设备在线' : '设备离线';
    const statusElement = deviceCard.find('.device-label');
    
    if (statusElement.length) {
        statusElement.text(statusLabel);
    }
    
    const statusContainer = deviceCard.find('.device-status');
    statusContainer
        .removeClass('state-online state-offline')
        .addClass(status === 'online' ? 'state-online' : 'state-offline');
    
    const pulseDot = deviceCard.find('.pulse-dot');
    if (pulseDot.length) {
        if (status === 'online') {
            pulseDot.removeClass('is-offline');
        } else {
            pulseDot.addClass('is-offline');
        }
    }
    
    console.log('设备信息已更新:', data);
}

// Placeholder helper for uptime display.
function updateUptime() {
    // TODO: Replace mock uptime with backend data.
    // Temporary placeholder until API integration is complete.
    const uptimeElement = $('#uptime-value');
    if (uptimeElement.length) {
        // Mock value used as a placeholder.
        uptimeElement.text('3天 2小时');
    }
}

// Content area interactions.
function initContentArea() {
    // Handle quick action cards on the overview module.
    $(document).on('click', '.action-card', function() {
        const target = $(this).data('target');
        if (target) {
            loadModule(target);
            
            // Sync active state with the sidebar.
            updateSidebarActive(target);
        }
    });
    
    // Shortcut to open the ROS overview.
    $(document).on('click', '.quick-start-btn', function() {
        loadModule('ros/overview');
        updateSidebarActive('ros/overview');
    });
}

// Dynamic module loading lifecycle.
function loadModule(modulePath) {
    console.log(`加载模块: ${modulePath}`);

    const contentArea = $('.content-wrapper .content-area');

    // Show loading indicator.
    contentArea.html(`
        <div class="loading-container" style="display: flex; justify-content: center; align-items: center; min-height: 60vh;">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">加载中...</span>
            </div>
        </div>
    `);

    // Bust caches by appending a timestamp.
    const timestamp = Date.now();
    const moduleUrl = `modules/${modulePath}/index.html?t=${timestamp}`;
    const styleUrl = `modules/${modulePath}/style.css?t=${timestamp}`;

    // Load the module stylesheet first.
    loadModuleStyle(styleUrl);

    // Fetch and render the module markup.
    $.ajax({
        url: moduleUrl,
        type: 'GET',
        cache: false,  // Disable caching for module assets.
        success: function(data) {
            contentArea.html(data);
            currentModule = modulePath;

            // Update breadcrumb trail.
            updateBreadcrumb(modulePath);

            // Load module-specific script if present.
            loadModuleScript(modulePath);

            console.log(`模块 ${modulePath} 加载成功`);
        },
        error: function(xhr, status, error) {
            console.error(`模块加载失败: ${modulePath}`, error);
            contentArea.html(`
                <div class="error-container" style="display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 60vh; text-align: center;">
                    <i class="bi bi-exclamation-triangle" style="font-size: 64px; color: #ef4444; margin-bottom: 20px;"></i>
                    <h3 style="color: #0f172a; margin-bottom: 12px;">模块加载失败</h3>
                    <p style="color: #64748b;">路径: ${modulePath}</p>
                    <button class="btn btn-primary mt-3" onclick="loadModule('overview')">返回概览</button>
                </div>
            `);
        }
    });
}

// Load module stylesheet.
function loadModuleStyle(styleUrl) {
    // Remove any previously injected module stylesheet.
    $('link[data-module-style]').remove();

    // Inject the new stylesheet (timestamp already appended).
    $('<link>')
        .attr('rel', 'stylesheet')
        .attr('href', styleUrl)
        .attr('data-module-style', 'true')
        .appendTo('head');
}

// Load module script.
function loadModuleScript(modulePath) {
    // Bust caches when loading scripts.
    const timestamp = Date.now();
    const scriptUrl = `modules/${modulePath}/main.js?t=${timestamp}`;

    if (typeof window.moduleCleanup === 'function') {
        window.moduleCleanup();
        window.moduleCleanup = null;
    }

    // Remove any prior module script.
    $('script[data-module-script]').remove();

    // Fetch and execute the new script.
    $.ajax({
        url: scriptUrl,
        dataType: 'script',
        cache: false,  // Disable caching for module scripts.
        success: function() {
            console.log(`模块脚本加载成功: ${scriptUrl}`);

            // Run the module initializer if provided.
            if (typeof window.moduleInit === 'function') {
                window.moduleInit();
            }
        },
        error: function() {
            // Missing scripts are expected for static modules.
            console.log(`模块无脚本文件: ${scriptUrl}`);
        }
    });
}

// Update breadcrumb navigation.
function updateBreadcrumb(modulePath) {
    const moduleNames = {
        'overview': '概览',
        'logs': '日志',
        'storage': '存储管理',
        'network': '网络',
        'runtime': '运行中心',
        'terminal': '终端',
        'file-transfer': '文件传输',
        'ros/overview': 'ROS 概览',
        'ros/communication': '通信监控',
        'ros/operations': '采集与配置',
        'ros/ai-commander': 'ROS-AI'
    };
    
    const moduleName = moduleNames[modulePath] || modulePath;
    $('.trail-current').text(moduleName);
}

// Update sidebar active module state.
function updateSidebarActive(modulePath) {
    $('.submenu-item').removeClass('active');
    $(`.submenu-item[data-module="${modulePath}"]`).addClass('active');
}

// Admin mode lifecycle.
function toggleAdminMode() {
    if (!adminModeActive) {
        // Prompt for admin credentials before activation.
        promptAdminPassword();
    } else {
        // Attempt to sign out of admin mode.
        deactivateAdminMode().catch(err => {
            console.error('退出管理员模式失败:', err);
        });
    }
}

async function promptAdminPassword() {
    // TODO: Replace prompt with a secure password dialog.
    // Temporary placeholder using the native prompt.
    const password = prompt('请输入管理员密码 (root 密码):');
    
    if (!password) {
        return;
    }

    await verifyAdminPassword(password);
}

async function verifyAdminPassword(password) {
    if (adminModePending) {
        return;
    }

    const csrfToken = await ensureCsrfToken();
    if (!csrfToken) {
        toastr.error('CSRF Token 未初始化，请重新登录后再试', '错误');
        return;
    }

    adminModePending = true;

    try {
        if (typeof toastr !== 'undefined') {
            toastr.clear();
        }
        toastr.info('正在验证管理员权限...', '校验中');
        const { response, data } = await postJson(API_VERIFY_ADMIN, { password }, {
            headers: { 'X-CSRF-Token': csrfToken }
        });

        if (!response.ok || data?.success !== true) {
            const message = data?.message || (response.status === 401 ? '密码错误，请重试' : '管理员验证失败');
            toastr.error(message, '验证失败');
            return;
        }

        activateAdminMode();
    } catch (error) {
        if (error.name === 'AbortError') {
            toastr.error('管理员验证超时，请重试', '超时');
        } else {
            toastr.error('管理员验证出现异常，请检查网络', '异常');
        }
        console.error('管理员验证失败:', error);
    } finally {
        adminModePending = false;
    }
}

function activateAdminMode() {
    adminModeActive = true;
    
    // Update UI to reflect active admin mode.
    $('.admin-mode-toggle').addClass('active');
    $('.label-text').text('管理员');
    $('.label-status').text('已激活');
    $('.user-role').text('管理员');
    
    if (typeof toastr !== 'undefined') {
        toastr.clear();
    }
    toastr.success('管理员模式已激活', '成功');
    console.log('管理员模式激活');

    window.dispatchEvent(new CustomEvent('rosdeck:admin-mode-change', {
        detail: { active: true }
    }));
}

async function deactivateAdminMode() {
    if (adminModePending) {
        return;
    }
    const csrfToken = await ensureCsrfToken();
    if (!csrfToken) {
        if (typeof toastr !== 'undefined') {
            toastr.error('无法获取 CSRF Token，请稍后重试', '错误');
        }
        return;
    }

    adminModePending = true;
    try {
        if (typeof toastr !== 'undefined') {
            toastr.clear();
            toastr.info('正在退出管理员模式...', '处理中');
        }
        const { response, data } = await postJson(API_ADMIN_LOGOUT, {}, {
            headers: { 'X-CSRF-Token': csrfToken }
        });
        if (!response.ok || data?.success !== true) {
            const message = data?.message || `退出失败 (HTTP ${response.status})`;
            if (typeof toastr !== 'undefined') {
                toastr.error(message, '错误');
            }
            return;
        }

        adminModeActive = false;
        
        // Update UI to reflect deactivated admin mode.
        $('.admin-mode-toggle').removeClass('active');
        $('.label-text').text('管理员');
        $('.label-status').text('未激活');
        $('.user-role').text('普通用户');
        
        if (typeof toastr !== 'undefined') {
            toastr.clear();
        }
        toastr.info('管理员模式已退出', '提示');
        console.log('管理员模式退出');
        window.dispatchEvent(new CustomEvent('rosdeck:admin-mode-change', {
            detail: { active: false }
        }));
    } catch (error) {
        if (typeof toastr !== 'undefined') {
            toastr.error('退出管理员模式时出现异常，请稍后重试', '错误');
        }
        throw error;
    } finally {
        adminModePending = false;
    }
}

// Power management actions.
async function handlePowerAction(action) {
    const actionText = action === 'restart' ? '重启系统' : '关闭系统';
    
    if (!confirm(`确定要${actionText}吗？`)) {
        return;
    }

    if (!adminModeActive) {
        toastr.warning('需要管理员权限', '提示');
        await promptAdminPassword();
        if (!adminModeActive) {
            return;
        }
    }

    if (powerActionPending) {
        toastr.info('已有电源操作正在执行，请稍候', '提示');
        return;
    }

    const csrfToken = await ensureCsrfToken();
    if (!csrfToken) {
        toastr.error('无法获取 CSRF Token，请刷新页面后重试', '错误');
        return;
    }

    powerActionPending = true;
    if (typeof toastr !== 'undefined') {
        toastr.clear();
    }
    toastr.info(`正在${actionText}...`, '操作中');

    try {
        const { response, data } = await postJson('/api/system/power', { action }, {
            headers: { 'X-CSRF-Token': csrfToken }
        });

        if (!response.ok || data?.success !== true) {
            const message = data?.message || `执行${actionText}失败`;
            toastr.error(message, '失败');
            console.error(`电源操作失败: ${action}`, { status: response.status, data });
            return;
        }

        const successMessage = data.message || `${actionText}命令已发送`;
        toastr.success(successMessage, '成功');
        console.log(`电源操作已触发: ${action}`, data);
    } catch (error) {
        if (error.name === 'AbortError') {
            toastr.error(`${actionText}请求超时，请重试`, '超时');
        } else {
            toastr.error(`执行${actionText}时出现异常`, '异常');
        }
        console.error('电源操作异常:', error);
    } finally {
        powerActionPending = false;
    }
}

// Responsive sidebar helpers.
function toggleSidebar() {
    $('.sidebar').toggleClass('open');
}

// Allow mobile toggles to open the sidebar.
$(document).on('click', '.menu-toggle', function() {
    toggleSidebar();
});

// Cleanup handlers.
window.addEventListener('beforeunload', function() {
    // Clear any active timers.
    if (statusUpdateInterval) {
        clearInterval(statusUpdateInterval);
    }
});
async function ensureCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    let token = meta ? meta.getAttribute('content') : null;

    if (!token) {
        try {
            const stored = sessionStorage.getItem(STORAGE_KEYS.csrf);
            if (stored) {
                token = stored;
                if (meta) {
                    meta.setAttribute('content', stored);
                }
            }
        } catch (err) {
            token = null;
        }
    }

    if (token) {
        try { sessionStorage.setItem(STORAGE_KEYS.csrf, token); } catch (err) {}
        return token;
    }

    try {
        const response = await fetch('/api/csrf-token', { credentials: 'include' });
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const payload = await response.json();
        token = payload?.token || null;
        if (token) {
            if (meta) {
                meta.setAttribute('content', token);
            }
            try { sessionStorage.setItem(STORAGE_KEYS.csrf, token); } catch (err) {}
        }
        return token;
    } catch (err) {
        console.error('获取 CSRF Token 失败:', err);
        return null;
    }
}

async function postJson(url, payload, { headers = {}, signal } = {}) {
    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...headers
        },
        body: JSON.stringify(payload),
        credentials: 'include',
        signal
    });
    let data = null;
    try {
        data = await response.clone().json();
    } catch (err) {
        data = null;
    }
    return { response, data };
}
