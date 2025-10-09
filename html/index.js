/**
 * RosDeck 主页面逻辑
 * 负责页面切换、状态更新、用户交互等功能
 */

// ==================== 全局变量 ====================
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

// ==================== 页面加载完成 ====================
$(document).ready(function() {
    console.log('RosDeck 初始化...');

    // 同步 CSRF Token（如从登录页传递）
    ensureCsrfToken().catch(err => {
        console.error('初始化 CSRF Token 失败:', err);
    });
    
    // 初始化各个模块
    initSidebar();
    initTopBar();
    initStatusRibbon();
    initContentArea();
    
    // 加载默认页面（概览）
    loadModule('overview');
    
    // 启动状态更新定时器
    startStatusUpdate();
    startDeviceInfoUpdates();
});

// ==================== 侧边栏功能 ====================
function initSidebar() {
    // 侧边栏菜单点击事件
    $('.submenu-item').on('click', function() {
        const modulePath = $(this).data('module');
        if (modulePath) {
            loadModule(modulePath);
            
            // 更新激活状态
            $('.submenu-item').removeClass('active');
            $(this).addClass('active');
        }
    });
    
    // 重启/关机按钮悬停控制
    let powerMenuTimeout;
    
    $('.power-btn').on('mouseenter', function() {
        clearTimeout(powerMenuTimeout);
        $('.power-dropdown').stop().fadeIn(200);
    });
    
    $('.power-btn').on('mouseleave', function() {
        powerMenuTimeout = setTimeout(function() {
            $('.power-dropdown').stop().fadeOut(200);
        }, 300); // 300ms 延迟，给用户时间移动鼠标
    });
    
    // 重启/关机选项点击
    $('.power-option').on('click', function(e) {
        e.stopPropagation();
        const action = $(this).data('action');
        $('.power-dropdown').fadeOut(200);
        handlePowerAction(action);
    });
    
    // 设备ID编辑功能（预留）
    $('.btn-edit').on('click', function() {
        if ($(this).data('editable') === 'true') {
            // TODO: 实现设备ID编辑功能
            console.log('设备ID编辑功能待实现');
        }
    });
}

// ==================== 顶部栏功能 ====================
function initTopBar() {
    // 通知按钮
    $('.notification-btn').on('click', function() {
        // TODO: 显示通知面板
        console.log('通知功能待实现');
        toastr.info('通知功能开发中...', '提示');
    });
    
    // 用户卡片点击
    $('.user-card').on('click', function() {
        // TODO: 显示用户菜单
        console.log('用户菜单待实现');
    });
    
    // 管理员模式切换
    $('.admin-mode-toggle').on('click', function() {
        toggleAdminMode();
    });
}

// ==================== 状态栏功能 ====================
function initStatusRibbon() {
    // 初始化完成，等待定时更新
    console.log('状态栏初始化完成');
}

// 启动状态更新
function startStatusUpdate() {
    // 首次更新
    updateSystemStatus();
    
    // 每5秒更新一次
    statusUpdateInterval = setInterval(updateSystemStatus, 5000);
}

// 更新系统状态
function updateSystemStatus() {
    fetch('/api/system/status')
        .then(response => response.json())
        .then(data => {
            // 更新运行时间
            const uptimeElement = $('#uptime-value');
            if (uptimeElement.length && data.uptime_seconds) {
                const uptime = formatUptime(data.uptime_seconds);
                uptimeElement.text(uptime);
            }
            
            // 更新磁盘使用率
            const diskElement = $('#disk-value');
            if (diskElement.length && data.disk) {
                diskElement.text(data.disk.usage_percent + '%');
            }
            
            // 更新内存使用率
            const memoryElement = $('#memory-value');
            if (memoryElement.length && data.memory) {
                memoryElement.text(data.memory.usage_percent + '%');
            }
            
            // 更新 CPU 使用率
            const cpuElement = $('#cpu-value');
            if (cpuElement.length && data.cpu) {
                cpuElement.text(data.cpu.usage_percent + '%');
            }
            
            // 更新网络速度
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

// ==================== 设备信息 ====================
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

// 更新运行时间
function updateUptime() {
    // TODO: 从后端获取真实的系统运行时间
    // 这里仅作演示
    const uptimeElement = $('#uptime-value');
    if (uptimeElement.length) {
        // 模拟数据
        uptimeElement.text('3天 2小时');
    }
}

// ==================== 内容区功能 ====================
function initContentArea() {
    // 快速操作卡片点击事件（概览页面）
    $(document).on('click', '.action-card', function() {
        const target = $(this).data('target');
        if (target) {
            loadModule(target);
            
            // 更新侧边栏激活状态
            updateSidebarActive(target);
        }
    });
    
    // 快速开始按钮
    $(document).on('click', '.quick-start-btn', function() {
        loadModule('ros/overview');
        updateSidebarActive('ros/overview');
    });
}

// ==================== 模块加载 ====================
function loadModule(modulePath) {
    console.log(`加载模块: ${modulePath}`);
    
    const contentArea = $('.content-wrapper .content-area');
    
    // 显示加载状态
    contentArea.html(`
        <div class="loading-container" style="display: flex; justify-content: center; align-items: center; min-height: 60vh;">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">加载中...</span>
            </div>
        </div>
    `);
    
    // 加载模块HTML
    const moduleUrl = `modules/${modulePath}/index.html`;
    const styleUrl = `modules/${modulePath}/style.css`;
    
    // 先加载样式
    loadModuleStyle(styleUrl);
    
    // 加载HTML内容
    $.ajax({
        url: moduleUrl,
        type: 'GET',
        success: function(data) {
            contentArea.html(data);
            currentModule = modulePath;
            
            // 更新面包屑
            updateBreadcrumb(modulePath);
            
            // 加载模块JS（如果存在）
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

// 加载模块样式
function loadModuleStyle(styleUrl) {
    // 移除之前加载的模块样式
    $('link[data-module-style]').remove();
    
    // 加载新样式
    $('<link>')
        .attr('rel', 'stylesheet')
        .attr('href', styleUrl)
        .attr('data-module-style', 'true')
        .appendTo('head');
}

// 加载模块脚本
function loadModuleScript(modulePath) {
    const scriptUrl = `modules/${modulePath}/main.js`;

    if (typeof window.moduleCleanup === 'function') {
        window.moduleCleanup();
        window.moduleCleanup = null;
    }
    
    // 移除之前的模块脚本
    $('script[data-module-script]').remove();
    
    // 尝试加载新脚本
    $.ajax({
        url: scriptUrl,
        dataType: 'script',
        cache: true,
        success: function() {
            console.log(`模块脚本加载成功: ${scriptUrl}`);
            
            // 如果模块有初始化函数，调用它
            if (typeof window.moduleInit === 'function') {
                window.moduleInit();
            }
        },
        error: function() {
            // 脚本不存在是正常的，不报错
            console.log(`模块无脚本文件: ${scriptUrl}`);
        }
    });
}

// 更新面包屑导航
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

// 更新侧边栏激活状态
function updateSidebarActive(modulePath) {
    $('.submenu-item').removeClass('active');
    $(`.submenu-item[data-module="${modulePath}"]`).addClass('active');
}

// ==================== 管理员模式 ====================
function toggleAdminMode() {
    if (!adminModeActive) {
        // 激活管理员模式，需要输入密码
        promptAdminPassword();
    } else {
        // 退出管理员模式
        deactivateAdminMode().catch(err => {
            console.error('退出管理员模式失败:', err);
        });
    }
}

async function promptAdminPassword() {
    // TODO: 实现密码输入对话框
    // 这里暂时用简单的 prompt 演示
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
    
    // 更新UI
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
        
        // 更新UI
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

// ==================== 电源操作 ====================
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

// ==================== 响应式菜单 ====================
function toggleSidebar() {
    $('.sidebar').toggleClass('open');
}

// 移动端菜单按钮（如需要可在HTML中添加）
$(document).on('click', '.menu-toggle', function() {
    toggleSidebar();
});

// ==================== 清理函数 ====================
window.addEventListener('beforeunload', function() {
    // 清理定时器
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
