/**
 * Core logic for the network dashboard module, including monitoring and admin workflows.
 */

(() => {
    const API_BASE = "/api/network";
    const POLL_INTERVAL = 5000; // 5 seconds, aligned with global status polling

    // Module state
    const state = {
        moduleActive: false,
        isAdmin: false,
        interfaces: [],
        timeWindow: "5min",
        trafficChart: null,
        pollTimer: null,
        selectedInterface: null,
        configCollapsed: false
    };

    // Cached DOM nodes
    const elements = {};

    // Module lifecycle

    window.moduleInit = function() {
        console.log("Network模块初始化");
        state.moduleActive = true;

        cacheDom();
        bindEvents();
        initializeChart();

        // Subscribe to admin mode changes
        window.addEventListener("rosdeck:admin-mode-change", handleAdminModeChange);

        // Prime initial data loads
        loadInterfaces();
        loadConnections();
        startPolling();
    };

    window.moduleCleanup = function() {
        console.log("Network模块清理");
        state.moduleActive = false;

        stopPolling();
        destroyChart();
        unbindEvents();

        window.removeEventListener("rosdeck:admin-mode-change", handleAdminModeChange);

        // Reset module state
        Object.keys(state).forEach(key => {
            if (key !== "moduleActive") {
                state[key] = Array.isArray(state[key]) ? [] : null;
            }
        });
    };

    // Cache DOM references

    function cacheDom() {
        const $module = $(".network-module");

        elements.$interfacesList = $module.find("#interfaces-list");
        elements.$btnRefreshInterfaces = $module.find("#btn-refresh-interfaces");

        elements.$trafficChart = $module.find("#traffic-chart");
        elements.$uploadSpeed = $module.find("#upload-speed");
        elements.$downloadSpeed = $module.find("#download-speed");
        elements.$timeWindowBtns = $module.find(".btn-time-window");

        elements.$connectionsContent = $module.find("#connections-content");
        elements.$btnRefreshConnections = $module.find("#btn-refresh-connections");

        elements.$configPanel = $module.find("#config-panel");
        elements.$configBody = $module.find("#config-body");
        elements.$btnCollapseConfig = $module.find("#btn-collapse-config");

        elements.$configTabs = $module.find(".config-tab");
        elements.$selectInterface = $module.find("#select-interface");
        elements.$formIpConfig = $module.find("#form-ip-config");
        elements.$btnResetForm = $module.find("#btn-reset-form");

        elements.$formDiagnostic = $module.find("#form-diagnostic");
        elements.$diagnosticOutput = $module.find("#diagnostic-output");
        elements.$diagnosticResult = $module.find("#diagnostic-result");
    }

    // Event bindings

    function bindEvents() {
        elements.$btnRefreshInterfaces.on("click", loadInterfaces);
        elements.$btnRefreshConnections.on("click", loadConnections);
        elements.$timeWindowBtns.on("click", handleTimeWindowChange);
        elements.$btnCollapseConfig.on("click", toggleConfigPanel);

        elements.$configTabs.on("click", handleConfigTabChange);
        elements.$formIpConfig.on("submit", handleIpConfigSubmit);
        elements.$btnResetForm.on("click", resetIpConfigForm);
        elements.$formDiagnostic.on("submit", handleDiagnosticSubmit);

        // Delegate interface card interactions
        elements.$interfacesList.on("click", ".interface-card", handleInterfaceClick);
        elements.$interfacesList.on("click", ".btn-toggle-interface", handleInterfaceToggle);
    }

    function unbindEvents() {
        elements.$btnRefreshInterfaces.off("click");
        elements.$btnRefreshConnections.off("click");
        elements.$timeWindowBtns.off("click");
        elements.$btnCollapseConfig.off("click");
        elements.$configTabs.off("click");
        elements.$formIpConfig.off("submit");
        elements.$btnResetForm.off("click");
        elements.$formDiagnostic.off("submit");
        elements.$interfacesList.off("click");
    }

    // Data polling

    function startPolling() {
        if (state.pollTimer) return;

        state.pollTimer = setInterval(() => {
            if (state.moduleActive) {
                loadTrafficHistory();
                // Interface and connection lists rely on manual refresh
            }
        }, POLL_INTERVAL);

        // Kick off the first fetch immediately
        loadTrafficHistory();
    }

    function stopPolling() {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
        }
    }

    // Network interfaces

    async function loadInterfaces() {
        try {
            const response = await fetch(`${API_BASE}/interfaces`, {
                credentials: "include"
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();

            if (data.success) {
                state.interfaces = data.interfaces || [];
                renderInterfaces();
                updateInterfaceSelector();
            } else {
                throw new Error(data.message || "加载失败");
            }

        } catch (error) {
            console.error("加载网络接口失败:", error);
            toastr.error("无法加载网络接口", "错误");
            renderInterfacesError();
        }
    }

    function renderInterfaces() {
        elements.$interfacesList.empty();

        if (state.interfaces.length === 0) {
            elements.$interfacesList.html(`
                <div class="empty-message">
                    <i class="bi bi-ethernet"></i>
                    <p>未找到网络接口</p>
                </div>
            `);
            return;
        }

        state.interfaces.forEach(iface => {
            const $card = $(`
                <div class="interface-card ${iface.is_up ? "active" : "inactive"}" data-interface="${escapeHtml(iface.name)}">
                    <div class="interface-header">
                        <div class="interface-name">
                            <i class="bi ${iface.is_up ? "bi-ethernet" : "bi-slash-circle"}"></i>
                            <span>${escapeHtml(iface.name)}</span>
                        </div>
                        <div class="interface-status">
                            <span class="status-badge ${iface.is_up ? "up" : "down"}">
                                ${iface.is_up ? "UP" : "DOWN"}
                            </span>
                        </div>
                    </div>
                    <div class="interface-body">
                        <div class="interface-info">
                            <div class="info-item">
                                <span class="info-label">IPv4:</span>
                                <span class="info-value">${iface.ipv4 || "-"}</span>
                            </div>
                            <div class="info-item">
                                <span class="info-label">MAC:</span>
                                <span class="info-value">${iface.mac || "-"}</span>
                            </div>
                            <div class="info-item">
                                <span class="info-label">发送:</span>
                                <span class="info-value">${formatBytes(iface.bytes_sent)}</span>
                            </div>
                            <div class="info-item">
                                <span class="info-label">接收:</span>
                                <span class="info-value">${formatBytes(iface.bytes_recv)}</span>
                            </div>
                            <div class="info-item">
                                <span class="info-label">错误:</span>
                                <span class="info-value">发${iface.errout} 收${iface.errin}</span>
                            </div>
                            <div class="info-item">
                                <span class="info-label">丢包:</span>
                                <span class="info-value">发${iface.dropout} 收${iface.dropin}</span>
                            </div>
                        </div>
                    </div>
                    ${state.isAdmin ? `
                        <div class="interface-actions">
                            <button class="btn-toggle-interface btn-sm" data-interface="${escapeHtml(iface.name)}" data-enable="${!iface.is_up}">
                                ${iface.is_up ? "禁用" : "启用"}
                            </button>
                        </div>
                    ` : ""}
                </div>
            `);

            elements.$interfacesList.append($card);
        });
    }

    function renderInterfacesError() {
        elements.$interfacesList.html(`
            <div class="error-message">
                <i class="bi bi-exclamation-octagon"></i>
                <p>加载失败</p>
            </div>
        `);
    }

    function handleInterfaceClick(e) {
        // Prevent toggle button clicks from bubbling
        if ($(e.target).closest(".btn-toggle-interface").length) {
            return;
        }

        const $card = $(e.currentTarget);
        const interfaceName = $card.data("interface");

        // Highlight the selected interface
        $(".interface-card").removeClass("selected");
        $card.addClass("selected");

        state.selectedInterface = interfaceName;

        console.log("选中接口:", interfaceName);
        // Future hook: highlight this interface in the chart
    }

    async function handleInterfaceToggle(e) {
        e.stopPropagation();

        const $btn = $(e.currentTarget);
        const interfaceName = $btn.data("interface");
        const enable = $btn.data("enable") === true;
        const action = enable ? "启用" : "禁用";

        // Confirm potentially disruptive actions
        if (!enable) {
            const confirmed = confirm(`确定要${action}接口 ${interfaceName} ?\n\n警告: 如果这是当前管理接口,操作后可能失去连接!`);
            if (!confirmed) return;
        }

        try {
            const csrfToken = await ensureCsrfToken();
            if (!csrfToken) {
                toastr.error("无法获取CSRF Token", "错误");
                return;
            }

            toastr.info(`正在${action}接口 ${interfaceName}...`, "处理中");

            const response = await fetch(`${API_BASE}/interface/toggle`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken
                },
                credentials: "include",
                body: JSON.stringify({
                    interface: interfaceName,
                    enable: enable
                })
            });

            const data = await response.json();

            if (response.ok && data.success) {
                toastr.success(data.message || `接口${action}成功`, "成功");
                // Refresh the interface list
                await loadInterfaces();
            } else {
                toastr.error(data.message || `接口${action}失败`, "失败");
            }

        } catch (error) {
            console.error(`${action}接口失败:`, error);
            toastr.error(`${action}接口时发生异常`, "错误");
        }
    }

    function updateInterfaceSelector() {
        elements.$selectInterface.empty();
        elements.$selectInterface.append('<option value="">选择接口...</option>');

        state.interfaces.forEach(iface => {
            elements.$selectInterface.append(
                `<option value="${escapeHtml(iface.name)}">${escapeHtml(iface.name)}</option>`
            );
        });
    }

    // Traffic chart

    function initializeChart() {
        const ctx = elements.$trafficChart.get(0);
        if (!ctx) return;

        state.trafficChart = new Chart(ctx, {
            type: "line",
            data: {
                labels: [],
                datasets: [
                    {
                        label: "上传速率",
                        data: [],
                        borderColor: "#f87171",
                        backgroundColor: "rgba(248, 113, 113, 0.1)",
                        fill: true,
                        tension: 0.4
                    },
                    {
                        label: "下载速率",
                        data: [],
                        borderColor: "#60a5fa",
                        backgroundColor: "rgba(96, 165, 250, 0.1)",
                        fill: true,
                        tension: 0.4
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: "index",
                    intersect: false
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                const label = context.dataset.label || "";
                                const value = context.parsed.y || 0;
                                return `${label}: ${formatBytesPerSecond(value)}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        display: true,
                        title: {
                            display: false
                        },
                        ticks: {
                            maxRotation: 0,
                            maxTicksLimit: 10
                        }
                    },
                    y: {
                        display: true,
                        title: {
                            display: true,
                            text: "速率 (KB/s)"
                        },
                        beginAtZero: true,
                        ticks: {
                            callback: function(value) {
                                return (value / 1024).toFixed(0);
                            }
                        }
                    }
                }
            }
        });
    }

    function destroyChart() {
        if (state.trafficChart) {
            state.trafficChart.destroy();
            state.trafficChart = null;
        }
    }

    async function loadTrafficHistory() {
        try {
            const response = await fetch(`${API_BASE}/traffic-history?window=${state.timeWindow}`, {
                credentials: "include"
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();

            if (data.success && data.data_points) {
                updateChart(data.data_points);
                updateSpeedDisplay(data.data_points);
            }

        } catch (error) {
            console.error("加载流量历史失败:", error);
        }
    }

    function updateChart(dataPoints) {
        if (!state.trafficChart || !dataPoints || dataPoints.length === 0) {
            return;
        }

        const labels = dataPoints.map(point => {
            const date = new Date(point.timestamp);
            return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        });

        const uploadData = dataPoints.map(point => point.upload_bps);
        const downloadData = dataPoints.map(point => point.download_bps);

        state.trafficChart.data.labels = labels;
        state.trafficChart.data.datasets[0].data = uploadData;
        state.trafficChart.data.datasets[1].data = downloadData;
        state.trafficChart.update("none"); // Skip animations to minimize overhead
    }

    function updateSpeedDisplay(dataPoints) {
        if (!dataPoints || dataPoints.length === 0) {
            elements.$uploadSpeed.text("0 KB/s");
            elements.$downloadSpeed.text("0 KB/s");
            return;
        }

        // Display the latest sample
        const latest = dataPoints[dataPoints.length - 1];
        elements.$uploadSpeed.text(formatBytesPerSecond(latest.upload_bps));
        elements.$downloadSpeed.text(formatBytesPerSecond(latest.download_bps));
    }

    function handleTimeWindowChange(e) {
        const $btn = $(e.currentTarget);
        const window = $btn.data("window");

        if (window === state.timeWindow) return;

        state.timeWindow = window;
        elements.$timeWindowBtns.removeClass("active");
        $btn.addClass("active");

        loadTrafficHistory();
    }

    // Network connections

    async function loadConnections() {
        try {
            const response = await fetch(`${API_BASE}/connections`, {
                credentials: "include"
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            renderConnections(data);

        } catch (error) {
            console.error("加载网络连接失败:", error);
            toastr.error("无法加载网络连接", "错误");
            renderConnectionsError();
        }
    }

    function renderConnections(data) {
        elements.$connectionsContent.empty();

        if (!data.success) {
            // Lack of privileges: fall back to summary stats
            const summary = data.summary || {};
            elements.$connectionsContent.html(`
                <div class="connections-summary">
                    <p class="summary-note">
                        <i class="bi bi-info-circle"></i>
                        权限不足,仅显示汇总信息
                    </p>
                    <div class="summary-stats">
                        <div class="stat-item">
                            <span class="stat-label">TCP连接:</span>
                            <span class="stat-value">${summary.tcp_connections || 0}</span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-label">UDP连接:</span>
                            <span class="stat-value">${summary.udp_connections || 0}</span>
                        </div>
                        <div class="stat-item">
                            <span class="stat-label">总计:</span>
                            <span class="stat-value">${summary.total || 0}</span>
                        </div>
                    </div>
                </div>
            `);
            return;
        }

        // Render detailed connection rows
        const connections = data.connections || [];

        if (connections.length === 0) {
            elements.$connectionsContent.html(`
                <div class="empty-message">
                    <i class="bi bi-link-45deg"></i>
                    <p>当前无活动连接</p>
                </div>
            `);
            return;
        }

        const $table = $(`
            <div class="connections-table-wrapper">
                <table class="connections-table">
                    <thead>
                        <tr>
                            <th>类型</th>
                            <th>本地地址</th>
                            <th>远程地址</th>
                            <th>状态</th>
                            <th>进程</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        `);

        const $tbody = $table.find("tbody");

        connections.slice(0, 100).forEach(conn => {
            const $row = $(`
                <tr>
                    <td>${escapeHtml(conn.type)}</td>
                    <td>${escapeHtml(conn.local_addr)}</td>
                    <td>${escapeHtml(conn.remote_addr)}</td>
                    <td><span class="connection-status">${escapeHtml(conn.status)}</span></td>
                    <td>${conn.process ? escapeHtml(conn.process) : "-"}</td>
                </tr>
            `);
            $tbody.append($row);
        });

        if (connections.length > 100) {
            $tbody.append(`
                <tr class="table-note">
                    <td colspan="5">仅显示前100条连接</td>
                </tr>
            `);
        }

        elements.$connectionsContent.append($table);
    }

    function renderConnectionsError() {
        elements.$connectionsContent.html(`
            <div class="error-message">
                <i class="bi bi-exclamation-octagon"></i>
                <p>加载失败</p>
            </div>
        `);
    }

    // Admin configuration

    function handleAdminModeChange(event) {
        const isActive = event?.detail?.active || false;
        state.isAdmin = isActive;

        if (isActive) {
            elements.$configPanel.show();
        } else {
            elements.$configPanel.hide();
        }

        // Refresh interface list to toggle admin controls
        renderInterfaces();
    }

    function toggleConfigPanel() {
        state.configCollapsed = !state.configCollapsed;

        if (state.configCollapsed) {
            elements.$configBody.slideUp();
            elements.$btnCollapseConfig.html('<i class="bi bi-chevron-down"></i>');
        } else {
            elements.$configBody.slideDown();
            elements.$btnCollapseConfig.html('<i class="bi bi-chevron-up"></i>');
        }
    }

    function handleConfigTabChange(e) {
        const $tab = $(e.currentTarget);
        const tabName = $tab.data("tab");

        elements.$configTabs.removeClass("active");
        $tab.addClass("active");

        $(".config-tab-content").hide();
        $(`#tab-${tabName}`).show();
    }

    async function handleIpConfigSubmit(e) {
        e.preventDefault();

        const iface = elements.$selectInterface.val();
        const ipAddress = $("#input-ip").val().trim();
        const netmask = $("#input-netmask").val().trim();
        const gateway = $("#input-gateway").val().trim();
        const persistent = $("#check-persistent").is(":checked");

        if (!iface || !ipAddress || !netmask) {
            toastr.warning("请填写必填项", "输入错误");
            return;
        }

        // Prompt for confirmation
        let confirmMsg = `确定要配置接口 ${iface} ?\n\nIP: ${ipAddress}/${netmask}`;
        if (gateway) {
            confirmMsg += `\n网关: ${gateway}`;
        }
        confirmMsg += `\n模式: ${persistent ? "持久化" : "临时"}`;
        confirmMsg += `\n\n警告: 如果这是当前管理接口,配置错误可能失去连接!`;

        if (!confirm(confirmMsg)) {
            return;
        }

        try {
            const csrfToken = await ensureCsrfToken();
            if (!csrfToken) {
                toastr.error("无法获取CSRF Token", "错误");
                return;
            }

            toastr.info("正在配置IP地址...", "处理中");

            const response = await fetch(`${API_BASE}/interface/config`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken
                },
                credentials: "include",
                body: JSON.stringify({
                    interface: iface,
                    ip_address: ipAddress,
                    netmask: netmask,
                    gateway: gateway || null,
                    persistent: persistent
                })
            });

            const data = await response.json();

            if (response.ok && data.success) {
                toastr.success(data.message || "IP配置成功", "成功");
                if (data.warning) {
                    toastr.warning(data.warning, "注意", { timeOut: 10000 });
                }
                resetIpConfigForm();
                await loadInterfaces();
            } else {
                toastr.error(data.message || "IP配置失败", "失败");
            }

        } catch (error) {
            console.error("IP配置失败:", error);
            toastr.error("配置过程发生异常", "错误");
        }
    }

    function resetIpConfigForm() {
        elements.$formIpConfig.get(0).reset();
    }

    async function handleDiagnosticSubmit(e) {
        e.preventDefault();

        const target = $("#input-ping-target").val().trim();
        const count = parseInt($("#input-ping-count").val()) || 4;

        if (!target) {
            toastr.warning("请输入目标地址", "输入错误");
            return;
        }

        try {
            const csrfToken = await ensureCsrfToken();
            if (!csrfToken) {
                toastr.error("无法获取CSRF Token", "错误");
                return;
            }

            toastr.info(`正在 ping ${target}...`, "诊断中");
            elements.$diagnosticOutput.hide();
            elements.$diagnosticResult.text("");

            const response = await fetch(`${API_BASE}/diagnostic/ping`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken
                },
                credentials: "include",
                body: JSON.stringify({
                    target: target,
                    count: count
                })
            });

            const data = await response.json();

            elements.$diagnosticOutput.show();

            if (data.success) {
                elements.$diagnosticResult.text(data.output || "诊断完成,无输出");
                toastr.success(`Ping ${target} 成功`, "成功");
            } else {
                elements.$diagnosticResult.text(data.output || data.message || "诊断失败");
                toastr.warning(`Ping ${target} 失败`, "失败");
            }

        } catch (error) {
            console.error("Ping诊断失败:", error);
            toastr.error("诊断过程发生异常", "错误");
        }
    }

    // Utility functions

    async function ensureCsrfToken() {
        try {
            const stored = sessionStorage.getItem("rosdeck_csrf_token");
            if (stored) return stored;

            const response = await fetch("/api/csrf-token", { credentials: "include" });
            if (!response.ok) throw new Error("Failed to fetch CSRF token");

            const data = await response.json();
            const token = data.token;
            if (token) {
                sessionStorage.setItem("rosdeck_csrf_token", token);
            }
            return token;
        } catch (error) {
            console.error("获取CSRF Token失败:", error);
            return null;
        }
    }

    function formatBytes(bytes) {
        if (!bytes || bytes === 0) return "0 B";
        const k = 1024;
        const sizes = ["B", "KB", "MB", "GB", "TB"];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return (bytes / Math.pow(k, i)).toFixed(1) + " " + sizes[i];
    }

    function formatBytesPerSecond(bps) {
        if (!bps || bps === 0) return "0 KB/s";
        const kbps = bps / 1024;
        if (kbps < 1024) {
            return kbps.toFixed(1) + " KB/s";
        }
        return (kbps / 1024).toFixed(1) + " MB/s";
    }

    function escapeHtml(str) {
        if (typeof str !== "string") return "";
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

})();
