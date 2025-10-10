/**
 * Runtime Module - Process and Service Management
 */

(function() {
    "use strict";

    let processRefreshInterval = null;
    let allProcesses = [];
    let allServices = [];
    let isAdminMode = false;

    // Initialize admin mode from global state
    function initAdminMode() {
        // Check if global adminModeActive exists (set by index.js)
        if (typeof window.adminModeActive !== 'undefined') {
            isAdminMode = window.adminModeActive;
        }
        console.log("Runtime: Initialized admin mode:", isAdminMode);
        updateAdminUI();
    }

    // Handle admin mode change event
    function handleAdminModeChange(event) {
        const isActive = event?.detail?.active || false;
        console.log("Runtime: Admin mode changed to", isActive);

        isAdminMode = isActive;
        updateAdminUI();

        // Force re-render to show/hide buttons
        if ($("#processes-panel").hasClass("show active")) {
            filterAndRenderProcesses();
        }
        if ($("#services-panel").hasClass("show active")) {
            filterAndRenderServices();
        }
    }

    // Update UI based on admin mode
    function updateAdminUI() {
        const adminElements = document.querySelectorAll(".admin-only");
        console.log(`Runtime: Updating admin UI, mode=${isAdminMode}, found ${adminElements.length} elements`);

        adminElements.forEach(el => {
            if (isAdminMode) {
                // For table cells, use table-cell; for others, use block
                if (el.tagName === 'TD' || el.tagName === 'TH') {
                    el.style.display = 'table-cell';
                } else {
                    el.style.display = 'block';
                }
            } else {
                el.style.display = 'none';
            }
        });
    }

    // Get CSRF token
    function getCsrfToken() {
        return sessionStorage.getItem("rosdeck_csrf_token") || "";
    }

    // ==================== Process Management ====================

    function loadProcesses() {
        const sortBy = $("#process-sort").val() || "cpu";

        $.ajax({
            url: `/api/runtime/processes?sort_by=${sortBy}`,
            method: "GET",
            success: function(response) {
                if (response.success) {
                    allProcesses = response.processes || [];
                    filterAndRenderProcesses();
                }
            },
            error: function(xhr) {
                toastr.error("加载进程列表失败");
                console.error("Load processes error:", xhr);
            }
        });
    }

    function filterAndRenderProcesses() {
        const searchTerm = $("#process-search").val().toLowerCase();

        let filtered = allProcesses;
        if (searchTerm) {
            filtered = allProcesses.filter(p =>
                p.name.toLowerCase().includes(searchTerm) ||
                p.pid.toString().includes(searchTerm) ||
                p.username.toLowerCase().includes(searchTerm) ||
                p.cmdline.toLowerCase().includes(searchTerm)
            );
        }

        renderProcessTable(filtered);
        $("#process-count").text(`总计: ${filtered.length} 个进程`);
    }

    function renderProcessTable(processes) {
        const tbody = $("#process-tbody");
        tbody.empty();

        if (processes.length === 0) {
            tbody.append(`<tr><td colspan="8" class="text-center text-muted">无进程数据</td></tr>`);
            return;
        }

        processes.forEach(proc => {
            const statusBadge = getProcessStatusBadge(proc.status);
            const killBtn = isAdminMode
                ? `<button class="btn btn-sm btn-danger kill-process-btn" data-pid="${proc.pid}" data-name="${proc.name}">
                       <i class="bi bi-x-circle"></i>
                   </button>`
                : "";

            const row = `
                <tr>
                    <td>${proc.pid}</td>
                    <td><strong>${escapeHtml(proc.name)}</strong></td>
                    <td>${escapeHtml(proc.username)}</td>
                    <td><span class="badge bg-info">${proc.cpu_percent}%</span></td>
                    <td><span class="badge bg-warning text-dark">${proc.memory_percent}%</span></td>
                    <td>${statusBadge}</td>
                    <td class="text-truncate" style="max-width: 300px;" title="${escapeHtml(proc.cmdline)}">
                        ${escapeHtml(proc.cmdline)}
                    </td>
                    <td class="admin-only">${killBtn}</td>
                </tr>
            `;
            tbody.append(row);
        });

        // Bind kill button events
        $(".kill-process-btn").off("click").on("click", function() {
            const pid = $(this).data("pid");
            const name = $(this).data("name");
            killProcess(pid, name);
        });

        updateAdminUI();
    }

    function getProcessStatusBadge(status) {
        const statusMap = {
            "running": '<span class="badge bg-success">运行中</span>',
            "sleeping": '<span class="badge bg-secondary">睡眠</span>',
            "stopped": '<span class="badge bg-dark">停止</span>',
            "zombie": '<span class="badge bg-danger">僵尸</span>'
        };
        return statusMap[status] || `<span class="badge bg-light text-dark">${status}</span>`;
    }

    function killProcess(pid, name) {
        if (!confirm(`确定要终止进程 "${name}" (PID: ${pid}) 吗？`)) {
            return;
        }

        $.ajax({
            url: "/api/runtime/processes/kill",
            method: "POST",
            contentType: "application/json",
            headers: {
                "X-CSRF-Token": getCsrfToken()
            },
            data: JSON.stringify({ pid: pid }),
            success: function(response) {
                toastr.success(response.message || "进程已终止");
                loadProcesses();
            },
            error: function(xhr) {
                const msg = xhr.responseJSON?.detail || "终止进程失败";
                toastr.error(msg);
            }
        });
    }

    // ==================== Service Management ====================

    function loadServices() {
        console.log("Loading services...");
        $.ajax({
            url: "/api/runtime/services",
            method: "GET",
            success: function(response) {
                console.log("Services loaded:", response);
                if (response.success) {
                    allServices = response.services || [];
                    filterAndRenderServices();
                }
            },
            error: function(xhr) {
                console.error("Load services error:", xhr.status, xhr.responseText);
                toastr.error("加载服务列表失败: " + (xhr.responseJSON?.detail || xhr.statusText));
            }
        });
    }

    function filterAndRenderServices() {
        const searchTerm = $("#service-search").val().toLowerCase();
        const filterType = $("#service-filter").val();

        let filtered = allServices;

        if (searchTerm) {
            filtered = filtered.filter(s =>
                s.name.toLowerCase().includes(searchTerm)
            );
        }

        if (filterType !== "all") {
            filtered = filtered.filter(s => s.active === filterType);
        }

        renderServiceTable(filtered);
        $("#service-count").text(`总计: ${filtered.length} 个服务`);
    }

    function renderServiceTable(services) {
        const tbody = $("#service-tbody");
        tbody.empty();

        if (services.length === 0) {
            tbody.append(`<tr><td colspan="5" class="text-center text-muted">无服务数据</td></tr>`);
            return;
        }

        services.forEach(svc => {
            const activeBadge = getServiceActiveBadge(svc.active);

            const actionButtons = isAdminMode ? `
                <div class="btn-group btn-group-sm" role="group">
                    <button class="btn btn-success service-action-btn" data-name="${svc.name}" data-action="start" title="启动">
                        <i class="bi bi-play-fill"></i>
                    </button>
                    <button class="btn btn-warning service-action-btn" data-name="${svc.name}" data-action="restart" title="重启">
                        <i class="bi bi-arrow-clockwise"></i>
                    </button>
                    <button class="btn btn-danger service-action-btn" data-name="${svc.name}" data-action="stop" title="停止">
                        <i class="bi bi-stop-fill"></i>
                    </button>
                    <button class="btn btn-info service-action-btn" data-name="${svc.name}" data-action="enable" title="启用自启">
                        <i class="bi bi-check-circle"></i>
                    </button>
                    <button class="btn btn-secondary service-action-btn" data-name="${svc.name}" data-action="disable" title="禁用自启">
                        <i class="bi bi-x-circle"></i>
                    </button>
                </div>
            ` : "";

            const row = `
                <tr>
                    <td><strong>${escapeHtml(svc.name)}</strong></td>
                    <td><span class="badge bg-light text-dark">${svc.load}</span></td>
                    <td>${activeBadge}</td>
                    <td><span class="badge bg-light text-dark">${svc.sub}</span></td>
                    <td class="admin-only">${actionButtons}</td>
                </tr>
            `;
            tbody.append(row);
        });

        // Bind service action buttons
        $(".service-action-btn").off("click").on("click", function() {
            const serviceName = $(this).data("name");
            const action = $(this).data("action");
            performServiceAction(serviceName, action);
        });

        updateAdminUI();
    }

    function getServiceActiveBadge(active) {
        const activeMap = {
            "active": '<span class="badge bg-success">运行中</span>',
            "inactive": '<span class="badge bg-secondary">已停止</span>',
            "failed": '<span class="badge bg-danger">失败</span>',
            "activating": '<span class="badge bg-info">启动中</span>',
            "deactivating": '<span class="badge bg-warning">停止中</span>'
        };
        return activeMap[active] || `<span class="badge bg-light text-dark">${active}</span>`;
    }

    function performServiceAction(serviceName, action) {
        const actionText = {
            "start": "启动",
            "stop": "停止",
            "restart": "重启",
            "enable": "启用自启",
            "disable": "禁用自启"
        }[action] || action;

        if (!confirm(`确定要 ${actionText} 服务 "${serviceName}" 吗？`)) {
            return;
        }

        $.ajax({
            url: "/api/runtime/services/action",
            method: "POST",
            contentType: "application/json",
            headers: {
                "X-CSRF-Token": getCsrfToken()
            },
            data: JSON.stringify({
                service_name: serviceName,
                action: action
            }),
            success: function(response) {
                toastr.success(response.message || `${actionText}成功`);
                setTimeout(loadServices, 1000);
            },
            error: function(xhr) {
                const msg = xhr.responseJSON?.detail || `${actionText}失败`;
                toastr.error(msg);
            }
        });
    }

    // ==================== Utility Functions ====================

    function escapeHtml(text) {
        const map = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#039;"
        };
        return text.replace(/[&<>"']/g, m => map[m]);
    }

    // ==================== Event Handlers ====================

    function setupEventHandlers() {
        // Process controls
        $("#refresh-processes").on("click", loadProcesses);
        $("#process-search").on("input", filterAndRenderProcesses);
        $("#process-sort").on("change", loadProcesses);

        // Service controls
        $("#refresh-services").on("click", loadServices);
        $("#service-search").on("input", filterAndRenderServices);
        $("#service-filter").on("change", filterAndRenderServices);

        // Tab switching - Bootstrap 5 uses different event
        const servicesTab = document.getElementById('services-tab');
        if (servicesTab) {
            servicesTab.addEventListener('shown.bs.tab', function() {
                loadServices();
            });
        }

        // Also bind click event as fallback
        $("#services-tab").on("click", function() {
            setTimeout(loadServices, 100);
        });

        // Listen to admin mode changes on window (not document)
        window.addEventListener("rosdeck:admin-mode-change", handleAdminModeChange);
    }

    // ==================== Module Lifecycle ====================

    window.moduleInit = function() {
        console.log("Runtime module initialized");

        initAdminMode();
        setupEventHandlers();

        // Initial load
        loadProcesses();

        // Auto-refresh processes every 5 seconds
        processRefreshInterval = setInterval(function() {
            if ($("#processes-panel").hasClass("active")) {
                loadProcesses();
            }
        }, 5000);
    };

    window.moduleCleanup = function() {
        console.log("Runtime module cleanup");

        if (processRefreshInterval) {
            clearInterval(processRefreshInterval);
            processRefreshInterval = null;
        }

        // Remove admin mode change listener
        window.removeEventListener("rosdeck:admin-mode-change", handleAdminModeChange);

        // Unbind events
        $("#refresh-processes").off("click");
        $("#process-search").off("input");
        $("#process-sort").off("change");
        $("#refresh-services").off("click");
        $("#service-search").off("input");
        $("#service-filter").off("change");
        $("#services-tab").off("click");

        const servicesTab = document.getElementById('services-tab');
        if (servicesTab) {
            servicesTab.removeEventListener('shown.bs.tab', loadServices);
        }

        allProcesses = [];
        allServices = [];
    };

})();
