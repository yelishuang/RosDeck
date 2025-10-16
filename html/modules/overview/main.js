/**
 * Overview module logic for system and ROS statistics.
 */
// Module initialization
window.moduleInit = function() {
    console.log('概览模块初始化...');
    
    // Load initial data
    loadOverviewData();
    
    // Refresh every five seconds
    window.overviewInterval = setInterval(loadOverviewData, 5000);
};

// Load overview data
function loadOverviewData() {
    fetchRosStats()
        .then(updateRosStats)
        .catch(error => {
            console.error('加载概览数据失败:', error);
        });
}

// Fetch ROS statistics
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
            // Provide fallback values
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

// Update ROS metrics display
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

// Module teardown
window.moduleCleanup = function() {
    console.log('概览模块卸载...');
    
    // Clear polling interval
    if (window.overviewInterval) {
        clearInterval(window.overviewInterval);
        window.overviewInterval = null;
    }
};

// Quick action handlers
$(document).on('click', '.action-card', function() {
    const target = $(this).data('target');
    if (target && typeof loadModule === 'function') {
        loadModule(target);
    }
});

// Quick start button
$(document).on('click', '.quick-start-btn', function() {
    if (typeof loadModule === 'function') {
        loadModule('ros/overview');
    }
});
