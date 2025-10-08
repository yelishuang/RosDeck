/**
 * 概览页面逻辑
 * 负责显示系统概览信息和 ROS 统计数据
 */

// ==================== 模块初始化 ====================
window.moduleInit = function() {
    console.log('概览模块初始化...');
    
    // 首次加载数据
    loadOverviewData();
    
    // 每 5 秒更新一次
    window.overviewInterval = setInterval(loadOverviewData, 5000);
};

// ==================== 加载概览数据 ====================
function loadOverviewData() {
    // 同时请求 ROS 统计和设备信息
    Promise.all([
        fetchRosStats(),
        fetchDeviceInfo()
    ]).then(([rosData, deviceData]) => {
        updateRosStats(rosData);
        updateDeviceCard(deviceData);
    }).catch(error => {
        console.error('加载概览数据失败:', error);
    });
}

// ==================== 获取 ROS 统计数据 ====================
function fetchRosStats() {
    return fetch('/api/ros/stats')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return response.json();
        })
        .catch(error => {
            console.error('获取 ROS 统计失败:', error);
            // 返回默认值
            return {
                active_nodes: 0,
                topics_count: 0,
                services_count: 0,
                stability_percent: 0,
                ros_version: 'ROS 2',
                last_updated: new Date().toISOString()
            };
        });
}

// ==================== 获取设备信息 ====================
function fetchDeviceInfo() {
    return fetch('/api/device/info')
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return response.json();
        })
        .catch(error => {
            console.error('获取设备信息失败:', error);
            // 返回默认值
            return {
                hostname: 'Unknown',
                status: 'offline',
                os: 'Unknown',
                architecture: 'Unknown',
                ip_address: '0.0.0.0'
            };
        });
}

// ==================== 更新 ROS 统计显示 ====================
function updateRosStats(data = {}) {
  const toNum = v => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };

  $('#stat-active-nodes').text(toNum(data.active_nodes));
  $('#stat-topics-count').text(toNum(data.topics_count));
  $('#stat-services-count').text(toNum(data.services_count));

  const stability = Number(data.stability_percent);
  const $stab = $('#stat-stability');

  if (Number.isFinite(stability)) {
    $stab.text(stability.toFixed(1) + '%');
    $stab.css('color',
      stability >= 95 ? '#10b981' :
      stability >= 80 ? '#f59e0b' : '#ef4444'
    );
  } else {
    $stab.text('--').css('color', '');
  }

  console.log('ROS 统计已更新:', data);
}


// ==================== 更新设备卡片 ====================
function updateDeviceCard(data) {
    // 更新设备名称
    const deviceNameElement = $('.device-card .device-name');
    if (deviceNameElement.length) {
        deviceNameElement.text(data.hostname || 'Unknown');
    }
    
    // 更新设备状态
    const deviceStatusElement = $('.device-card .device-status');
    if (deviceStatusElement.length) {
        const status = data.status || 'offline';
        const statusText = status === 'online' ? '在线' : '离线';
        const statusClass = status === 'online' ? 'online' : 'offline';
        
        deviceStatusElement
            .text(statusText)
            .removeClass('online offline')
            .addClass(statusClass);
    }
    
    // 更新设备详细信息（如果有展开区域）
    const deviceInfoElement = $('.device-info');
    if (deviceInfoElement.length) {
        deviceInfoElement.html(`
            <div><strong>系统:</strong> ${data.os || 'Unknown'}</div>
            <div><strong>架构:</strong> ${data.architecture || 'Unknown'}</div>
            <div><strong>IP:</strong> ${data.ip_address || '0.0.0.0'}</div>
        `);
    }
    
    console.log('设备信息已更新:', data);
}

// ==================== 模块卸载清理 ====================
window.moduleCleanup = function() {
    console.log('概览模块卸载...');
    
    // 清除定时器
    if (window.overviewInterval) {
        clearInterval(window.overviewInterval);
        window.overviewInterval = null;
    }
};

// ==================== 快速操作卡片点击 ====================
$(document).on('click', '.action-card', function() {
    const target = $(this).data('target');
    if (target && typeof loadModule === 'function') {
        loadModule(target);
    }
});

// ==================== 快速开始按钮 ====================
$(document).on('click', '.quick-start-btn', function() {
    if (typeof loadModule === 'function') {
        loadModule('ros/overview');
    }
});