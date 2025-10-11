/**************************************************************************
 * ROS Operations Module
 * 采集与配置：参数管理、数据录制、Bag 管理、生命周期、Launch
 **************************************************************************/

(function() {
    "use strict";

    const PARAM_EVENT_INTERVAL = 5000;
    const RECORDING_REFRESH_INTERVAL = 4000;
    const PLAYBACK_REFRESH_INTERVAL = 5000;
    const LIFECYCLE_REFRESH_INTERVAL = 15000;
    const LAUNCH_REFRESH_INTERVAL = 12000;

    const state = {
        adminMode: false,
        activeTab: "parameters",
        parameterNodes: [],
        filteredNodes: [],
        currentNode: null,
        currentParameters: {},
        currentTree: {},
        parameterView: "flat",
        parameterFilter: "",
        parameterEventCursor: null,
        paramHighlightTimers: new Map(),

        topics: [],
        selectedTopics: new Set(),
        presets: [],
        activeRecordings: [],

        bags: [],
        playbacks: [],
        selectedExportFormat: "json",

        lifecycleNodes: [],

        launchFiles: [],
        launchIncludeGlobal: false,
        activeLaunches: [],
    };

    let pollers = {
        parameterEvents: null,
        recordings: null,
        playbacks: null,
        lifecycle: null,
        launches: null,
    };

    let adminModeHandler = null;
    let bootstrapModal = null;

    // ==================== Module Lifecycle ====================
    window.moduleInit = function moduleInit() {
        console.log("[ROS Operations] 初始化模块");
        state.adminMode = Boolean(window.adminModeActive);
        updateAdminUI();
        initModal();
        bindTabEvents();
        bindGlobalActions();
        bindParameterEvents();
        bindRecordingEvents();
        bindBagEvents();
        bindLifecycleEvents();
        bindLaunchEvents();
        registerAdminModeListener();

        // 初始加载
        refreshAll();

        // 启动轮询
        startPollers();
    };

    window.moduleCleanup = function moduleCleanup() {
        console.log("[ROS Operations] 清理模块");
        stopPollers();
        unregisterAdminModeListener();
        clearParameterHighlights();
        state.selectedTopics.clear();
    };

    function registerAdminModeListener() {
        if (adminModeHandler) {
            return;
        }
        adminModeHandler = (event) => {
            const active = Boolean(event?.detail?.active);
            state.adminMode = active;
            updateAdminUI();
        };
        window.addEventListener("rosdeck:admin-mode-change", adminModeHandler);
    }

    function unregisterAdminModeListener() {
        if (!adminModeHandler) {
            return;
        }
        window.removeEventListener("rosdeck:admin-mode-change", adminModeHandler);
        adminModeHandler = null;
    }

    function startPollers() {
        stopPollers();
        pollers.parameterEvents = setInterval(fetchParameterEvents, PARAM_EVENT_INTERVAL);
        pollers.recordings = setInterval(loadActiveRecordings, RECORDING_REFRESH_INTERVAL);
        pollers.playbacks = setInterval(loadPlaybacks, PLAYBACK_REFRESH_INTERVAL);
        pollers.lifecycle = setInterval(loadLifecycleNodes, LIFECYCLE_REFRESH_INTERVAL);
        pollers.launches = setInterval(loadActiveLaunches, LAUNCH_REFRESH_INTERVAL);
    }

    function stopPollers() {
        Object.values(pollers).forEach(timer => {
            if (timer) {
                clearInterval(timer);
            }
        });
        pollers = {
            parameterEvents: null,
            recordings: null,
            playbacks: null,
            lifecycle: null,
            launches: null,
        };
    }

    function refreshAll() {
        Promise.all([
            loadParameterNodes(),
            loadTopics(),
            loadRecordingPresets(),
            loadActiveRecordings(),
            loadBagList(),
            loadPlaybacks(),
            loadLifecycleNodes(),
            loadLaunchFiles(),
            loadActiveLaunches(),
        ]).catch(err => console.error("初始化加载失败", err));
    }

    // ==================== UI Helpers ====================
    function initModal() {
        const modalElement = document.getElementById("ros-operations-modal");
        if (window.bootstrap && modalElement) {
            bootstrapModal = new bootstrap.Modal(modalElement, { backdrop: true, keyboard: true });
        }
    }

    function showModal(title, content) {
        const modalElement = document.getElementById("ros-operations-modal");
        if (!modalElement) {
            alert(content);
            return;
        }
        const titleEl = modalElement.querySelector(".modal-title");
        const preEl = modalElement.querySelector(".modal-content-pre");
        titleEl.textContent = title;
        preEl.textContent = content;
        if (bootstrapModal) {
            bootstrapModal.show();
        } else {
            modalElement.style.display = "block";
        }
    }

    function updateAdminUI() {
        document.querySelectorAll(".admin-only").forEach(el => {
            el.style.display = state.adminMode ? "" : "none";
        });
    }

    function bindTabEvents() {
        document.querySelectorAll(".operations-tabs .tab-link").forEach(button => {
            button.addEventListener("click", () => {
                const target = button.dataset.tab;
                if (!target || state.activeTab === target) {
                    return;
                }
                state.activeTab = target;
                document.querySelectorAll(".operations-tabs .tab-link").forEach(btn => btn.classList.toggle("active", btn === button));
                document.querySelectorAll(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === `tab-${target}`));
            });
        });
    }

    function bindGlobalActions() {
        document.querySelector('[data-action="refresh-all"]')?.addEventListener("click", () => {
            refreshAll();
            notify("success", "已刷新全部数据");
        });
    }

    function getCsrfToken() {
        return sessionStorage.getItem("rosdeck_csrf_token") || "";
    }

    function notify(type, message) {
        if (window.toastr && typeof window.toastr[type] === "function") {
            window.toastr[type](message);
        } else {
            console.log(`[${type}]`, message);
        }
    }

    function formatDuration(seconds) {
        if (!seconds) {
            return "0s";
        }
        const s = Math.floor(seconds % 60);
        const m = Math.floor((seconds / 60) % 60);
        const h = Math.floor(seconds / 3600);
        const parts = [];
        if (h) parts.push(`${h}h`);
        if (m) parts.push(`${m}m`);
        parts.push(`${s}s`);
        return parts.join(" ");
    }

    function formatBytes(bytes) {
        if (!bytes && bytes !== 0) {
            return "-";
        }
        const units = ["B", "KB", "MB", "GB", "TB"];
        let size = bytes;
        let unit = 0;
        while (size >= 1024 && unit < units.length - 1) {
            size /= 1024;
            unit += 1;
        }
        return `${size.toFixed(unit === 0 ? 0 : 2)} ${units[unit]}`;
    }

    function formatTimestamp(epochSeconds) {
        if (!epochSeconds) return "-";
        return new Date(epochSeconds * 1000).toLocaleString();
    }

    function extractFilename(response) {
        const disposition = response.headers.get("Content-Disposition");
        if (!disposition) {
            return null;
        }
        const match = disposition.match(/filename="?([^\";]+)"?/i);
        return match ? decodeURIComponent(match[1]) : null;
    }

    function downloadBlob(blob, filename) {
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename || "download.bin";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    }

    // ==================== 参数管理 ====================
    function bindParameterEvents() {
        const nodeSearch = document.getElementById("parameter-node-search");
        if (nodeSearch) {
            nodeSearch.addEventListener("input", () => {
                filterParameterNodes(nodeSearch.value);
            });
        }
        const filterInput = document.getElementById("parameter-filter");
        if (filterInput) {
            filterInput.addEventListener("input", () => {
                state.parameterFilter = filterInput.value.trim().toLowerCase();
                renderParameterTable();
                renderParameterTree();
            });
        }
        document.querySelectorAll("[data-view-mode]").forEach(button => {
            button.addEventListener("click", () => {
                const mode = button.dataset.viewMode;
                if (!mode || state.parameterView === mode) return;
                state.parameterView = mode;
                document.querySelectorAll("[data-view-mode]").forEach(btn => btn.classList.toggle("active", btn === button));
                document.querySelector(".parameter-table-view")?.classList.toggle("d-none", mode !== "flat");
                document.querySelector(".parameter-tree-view")?.classList.toggle("d-none", mode === "flat");
                renderParameterTable();
                renderParameterTree();
            });
        });
        document.querySelector('[data-action="export-params"]')?.addEventListener("click", () => {
            window.open("/api/ros/config/parameters/snapshot", "_blank");
        });
    }

    async function loadParameterNodes() {
        try {
            const resp = await fetch("/api/ros/config/parameters/nodes", { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.parameterNodes = data.nodes || [];
            state.filteredNodes = state.parameterNodes;
            renderParameterNodeList();
            document.getElementById("parameter-status-dot")?.classList.toggle("online", Boolean(data.available));
            document.getElementById("parameter-status-text").textContent = data.available ? "数据实时同步中" : "ROS 参数服务不可用";
        } catch (error) {
            console.error("加载参数节点失败", error);
            notify("error", "加载参数节点失败");
        }
    }

    function filterParameterNodes(keyword) {
        const lower = keyword.trim().toLowerCase();
        if (!lower) {
            state.filteredNodes = state.parameterNodes;
        } else {
            state.filteredNodes = state.parameterNodes.filter(node =>
                node.full_name.toLowerCase().includes(lower) ||
                node.name.toLowerCase().includes(lower)
            );
        }
        renderParameterNodeList();
    }

    function renderParameterNodeList() {
        const list = document.getElementById("parameter-node-list");
        const empty = document.getElementById("parameter-node-empty");
        if (!list) return;
        list.innerHTML = "";
        if (!state.filteredNodes.length) {
            empty.style.display = "block";
            return;
        }
        empty.style.display = "none";
        state.filteredNodes.forEach(node => {
            const item = document.createElement("li");
            item.className = "list-group-item list-group-item-action";
            item.dataset.node = node.full_name;
            item.innerHTML = `
                <span class="node-label">${node.full_name}</span>
                <span class="badge bg-light text-dark">${node.parameter_count ?? 0}</span>
            `;
            if (state.currentNode === node.full_name) {
                item.classList.add("active");
            }
            item.addEventListener("click", () => onSelectParameterNode(node.full_name));
            list.appendChild(item);
        });
    }

    async function onSelectParameterNode(nodeFullName) {
        if (!nodeFullName || state.currentNode === nodeFullName) {
            return;
        }
        state.currentNode = nodeFullName;
        document.getElementById("parameter-current-node").textContent = nodeFullName;
        document.querySelectorAll("#parameter-node-list .list-group-item").forEach(el => {
            el.classList.toggle("active", el.dataset.node === nodeFullName);
        });
        try {
            const resp = await fetch(`/api/ros/config/parameters/details?node=${encodeURIComponent(nodeFullName)}`, { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.currentParameters = data.flat || {};
            state.currentTree = data.tree || {};
            document.getElementById("parameter-count-badge").textContent = `${Object.keys(state.currentParameters).length} 个参数`;
            renderParameterTable();
            renderParameterTree();
        } catch (error) {
            console.error("加载节点参数失败", error);
            notify("error", `加载节点参数失败：${error.message}`);
        }
    }

    function renderParameterTable() {
        const tbody = document.getElementById("parameter-table-body");
        if (!tbody) return;

        if (!state.currentNode) {
            tbody.innerHTML = `<tr class="text-muted"><td colspan="4" class="text-center py-4">选择节点以查看参数</td></tr>`;
            return;
        }

        const filter = state.parameterFilter;
        const rows = [];
        Object.entries(state.currentParameters).forEach(([name, info]) => {
            if (name === "__meta__") return;
            if (filter && !name.toLowerCase().includes(filter)) return;
            const value = info?.value;
            rows.push({
                name,
                type: info?.type || "-",
                description: info?.descriptor?.description || "",
                readOnly: info?.descriptor?.read_only,
                value: value,
            });
        });
        if (!rows.length) {
            tbody.innerHTML = `<tr class="text-muted"><td colspan="4" class="text-center py-4">无匹配参数</td></tr>`;
            return;
        }
        const fragment = document.createDocumentFragment();
        rows.sort((a, b) => a.name.localeCompare(b.name)).forEach(row => {
            const tr = document.createElement("tr");
            tr.dataset.paramName = row.name;
            tr.innerHTML = `
                <td>
                    <div class="fw-semibold">${row.name}</div>
                    ${row.readOnly ? '<span class="badge bg-light text-dark">只读</span>' : ""}
                </td>
                <td><span class="badge bg-secondary">${row.type}</span></td>
                <td><code>${formatParameterValue(row.value)}</code></td>
                <td class="text-muted">${row.description || "-"}</td>
            `;
            fragment.appendChild(tr);
        });
        tbody.innerHTML = "";
        tbody.appendChild(fragment);
    }

    function renderParameterTree() {
        const container = document.getElementById("parameter-tree-root");
        if (!container) return;
        container.innerHTML = "";
        if (!state.currentNode) {
            container.innerHTML = `<div class="tree-empty text-muted">选择节点以查看树形参数视图</div>`;
            return;
        }
        const tree = state.currentTree || {};
        const filter = state.parameterFilter;

        const fragment = document.createDocumentFragment();
        Object.entries(tree).forEach(([key, node]) => {
            const branch = renderTreeNode(key, node, filter);
            if (branch) fragment.appendChild(branch);
        });
        if (!fragment.children.length) {
            container.innerHTML = `<div class="tree-empty text-muted">没有匹配的参数</div>`;
        } else {
            container.appendChild(fragment);
        }
    }

    function renderTreeNode(key, node, filter) {
        if (!node) return null;
        const element = document.createElement("div");
        element.className = "tree-node";

        const matches = !filter || key.toLowerCase().includes(filter);
        if (node.is_leaf) {
            if (!matches) return null;
            element.innerHTML = `
                <div class="tree-label">
                    <span class="fw-semibold">${key}</span>
                    <span class="tree-badge badge bg-secondary">${node.parameter?.type || "-"}</span>
                </div>
                <div class="tree-value"><code>${formatParameterValue(node.parameter?.value)}</code></div>
                <div class="text-muted small">${node.parameter?.descriptor?.description || ""}</div>
            `;
            return element;
        }

        const children = node.children || {};
        const fragment = document.createDocumentFragment();
        Object.entries(children).forEach(([childKey, childNode]) => {
            const childElement = renderTreeNode(childKey, childNode, filter);
            if (childElement) fragment.appendChild(childElement);
        });
        if (!fragment.children.length && !matches) {
            return null;
        }
        element.innerHTML = `
            <div class="tree-label fw-semibold">${key}</div>
        `;
        element.appendChild(fragment);
        return element;
    }

    function formatParameterValue(value) {
        if (value === null || value === undefined) {
            return "null";
        }
        if (typeof value === "object") {
            try {
                return JSON.stringify(value);
            } catch (err) {
                return String(value);
            }
        }
        return String(value);
    }

    async function fetchParameterEvents() {
        try {
            const params = state.parameterEventCursor ? `?since=${state.parameterEventCursor}` : "";
            const resp = await fetch(`/api/ros/config/parameters/events${params}`, { credentials: "include" });
            if (!resp.ok) return;
            const data = await resp.json();
            const events = data.events || [];
            if (!events.length) return;
            state.parameterEventCursor = Math.max(...events.map(ev => ev.timestamp));
            events.forEach(event => applyParameterEvent(event));
        } catch (error) {
            console.debug("参数事件轮询失败", error);
        }
    }

    function applyParameterEvent(event) {
        if (!event || !state.currentNode || event.node !== state.currentNode) {
            return;
        }
        const applyItem = (item) => {
            if (!item) return;
            state.currentParameters[item.name] = item;
            highlightParameterRow(item.name);
        };
        const removeItem = (item) => {
            if (!item) return;
            delete state.currentParameters[item.name];
        };
        (event.added || []).forEach(applyItem);
        (event.changed || []).forEach(applyItem);
        (event.deleted || []).forEach(removeItem);
        renderParameterTable();
        renderParameterTree();
        const badge = document.getElementById("parameter-count-badge");
        if (badge) {
            const length = Object.keys(state.currentParameters).filter(name => name !== "__meta__").length;
            badge.textContent = `${length} 个参数`;
        }
    }

    function highlightParameterRow(paramName) {
        if (!paramName) return;
        const rows = document.querySelectorAll(`[data-param-name="${CSS.escape(paramName)}"]`);
        rows.forEach(row => {
            row.classList.add("parameter-row-updated");
            if (state.paramHighlightTimers.has(row)) {
                clearTimeout(state.paramHighlightTimers.get(row));
            }
            const timer = setTimeout(() => {
                row.classList.remove("parameter-row-updated");
                state.paramHighlightTimers.delete(row);
            }, 1800);
            state.paramHighlightTimers.set(row, timer);
        });
    }

    function clearParameterHighlights() {
        state.paramHighlightTimers.forEach(timer => clearTimeout(timer));
        state.paramHighlightTimers.clear();
    }

    // ==================== 数据录制 ====================
    function bindRecordingEvents() {
        document.querySelector('[data-action="start-recording"]')?.addEventListener("click", onStartRecording);
        document.querySelector('[data-action="stop-all-recordings"]')?.addEventListener("click", stopAllRecordings);
        const customTopics = document.getElementById("recording-custom-topics");
        if (customTopics) {
            customTopics.addEventListener("focusout", syncCustomTopics);
        }
    }

    async function loadTopics() {
        try {
            const resp = await fetch("/api/ros/config/topics", { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.topics = Array.isArray(data.topics) ? data.topics : [];
            renderTopicSelector();
        } catch (error) {
            console.error("加载话题列表失败", error);
            notify("error", "加载话题列表失败");
            renderTopicSelector(true);
        }
    }

    function renderTopicSelector(loadFailed = false) {
        const container = document.getElementById("recording-topic-list");
        if (!container) return;
        container.innerHTML = "";
        if (loadFailed) {
            container.innerHTML = `<div class="empty-placeholder text-danger">话题列表加载失败，可使用下方自定义输入。</div>`;
            return;
        }
        if (!state.topics.length) {
            container.innerHTML = `<div class="empty-placeholder">未检测到话题，请检查 ROS 网络。</div>`;
            return;
        }
        state.topics.sort((a, b) => a.name.localeCompare(b.name));
        state.topics.forEach(topic => {
            const id = `topic-${topic.name.replace(/[^\w]/g, "_")}`;
            const wrapper = document.createElement("div");
            wrapper.className = "form-check";
            wrapper.innerHTML = `
                <input class="form-check-input" type="checkbox" value="${topic.name}" id="${id}">
                <label class="form-check-label" for="${id}">
                    <span class="fw-semibold">${topic.name}</span>
                    <small class="text-muted">${topic.type || ""} · Pub ${topic.publisher_count || 0} / Sub ${topic.subscriber_count || 0}</small>
                </label>
            `;
            const checkbox = wrapper.querySelector("input");
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) {
                    state.selectedTopics.add(topic.name);
                } else {
                    state.selectedTopics.delete(topic.name);
                }
            });
            container.appendChild(wrapper);
        });
    }

    async function loadRecordingPresets() {
        try {
            const resp = await fetch("/api/ros/config/recordings/presets", { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.presets = data.presets || [];
            renderRecordingPresets();
        } catch (error) {
            console.error("加载录制预设失败", error);
            notify("warning", "录制预设加载失败");
        }
    }

    function renderRecordingPresets() {
        const container = document.getElementById("recording-presets");
        if (!container) return;
        container.innerHTML = "";
        if (!state.presets.length) {
            container.innerHTML = `<div class="empty-placeholder text-muted">暂无预设，可直接勾选话题创建录制任务。</div>`;
            return;
        }
        state.presets.forEach(preset => {
            const item = document.createElement("div");
            item.className = "preset-item";
            item.dataset.topics = JSON.stringify(preset.topics || []);
            item.innerHTML = `
                <div class="d-flex justify-content-between align-items-center">
                    <span class="fw-semibold">${preset.label || preset.id}</span>
                    <i class="bi bi-chevron-right text-muted"></i>
                </div>
                <div class="preset-topics text-truncate">${(preset.topics || []).join(", ")}</div>
                <div class="text-muted small">${preset.description || ""}</div>
            `;
            item.addEventListener("click", () => applyPresetTopics(preset));
            container.appendChild(item);
        });
    }

    function applyPresetTopics(preset) {
        if (!preset || !Array.isArray(preset.topics)) return;
        state.selectedTopics = new Set(preset.topics);
        // 更新勾选状态
        document.querySelectorAll("#recording-topic-list input[type=checkbox]").forEach(input => {
            input.checked = state.selectedTopics.has(input.value);
        });
        document.getElementById("recording-custom-topics").value = "";
        notify("info", `已应用预设 ${preset.label || preset.id}`);
    }

    function syncCustomTopics() {
        const input = document.getElementById("recording-custom-topics");
        if (!input) return;
        const topics = input.value.split(",").map(t => t.trim()).filter(Boolean);
        topics.forEach(topic => state.selectedTopics.add(topic));
    }

    function collectRecordingPayload() {
        syncCustomTopics();
        const durationInput = document.getElementById("recording-duration");
        const sizeInput = document.getElementById("recording-size-limit");
        const topics = Array.from(state.selectedTopics);
        const payload = {
            topics,
            duration_limit: durationInput?.value ? Number(durationInput.value) : undefined,
            size_limit_mb: sizeInput?.value ? Number(sizeInput.value) : undefined,
        };
        payload.topics = payload.topics.filter(Boolean);
        if (!payload.topics.length) {
            throw new Error("请至少选择一个话题");
        }
        return payload;
    }

    async function onStartRecording() {
        if (!state.adminMode) {
            notify("warning", "需要管理员模式才能启动录制");
            return;
        }
        let payload;
        try {
            payload = collectRecordingPayload();
        } catch (error) {
            notify("warning", error.message);
            return;
        }
        try {
            const resp = await fetch("/api/ros/config/recordings/start", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include",
                body: JSON.stringify(payload)
            });
            if (!resp.ok) {
                const error = await resp.json().catch(() => ({}));
                throw new Error(error?.detail?.message || `HTTP ${resp.status}`);
            }
            notify("success", "录制任务已启动");
            state.selectedTopics.clear();
            document.querySelectorAll("#recording-topic-list input[type=checkbox]").forEach(input => input.checked = false);
            document.getElementById("recording-custom-topics").value = "";
            await loadActiveRecordings();
        } catch (error) {
            console.error("启动录制失败", error);
            notify("error", `启动录制失败：${error.message}`);
        }
    }

    async function loadActiveRecordings() {
        try {
            const resp = await fetch("/api/ros/config/recordings/active", { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.activeRecordings = data.recordings || [];
            renderActiveRecordings();
        } catch (error) {
            console.error("加载录制状态失败", error);
        }
    }

    function renderActiveRecordings() {
        const tbody = document.getElementById("recording-active-body");
        if (!tbody) return;
        tbody.innerHTML = "";
        if (!state.activeRecordings.length) {
            tbody.innerHTML = `<tr class="text-muted"><td colspan="5" class="text-center py-4">暂无录制任务</td></tr>`;
            return;
        }
        state.activeRecordings.forEach(item => {
            const topics = Array.isArray(item.topics) ? item.topics : [];
            const previewTopics = topics.slice(0, 3).join("<br>");
            const hasMore = topics.length > 3 ? `<span class="text-muted">...</span>` : "";
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${previewTopics || "-"} ${hasMore}</td>
                <td>${formatDuration(item.elapsed_seconds)}</td>
                <td>${formatBytes(item.size_bytes)}</td>
                <td>${item.message_count ?? "-"}</td>
                <td class="admin-only">
                    <button class="btn btn-sm btn-outline-danger" data-action="stop-recording" data-id="${item.recording_id}">
                        <i class="bi bi-stop-circle"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
        updateAdminUI();
        tbody.querySelectorAll('[data-action="stop-recording"]').forEach(button => {
            button.addEventListener("click", () => stopRecording(button.dataset.id));
        });
    }

    async function stopRecording(recordingId) {
        if (!recordingId) return;
        try {
            const resp = await fetch("/api/ros/config/recordings/stop", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include",
                body: JSON.stringify({ recording_id: recordingId })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            notify("success", "录制任务已停止");
            await loadActiveRecordings();
            await loadBagList();
        } catch (error) {
            console.error("停止录制失败", error);
            notify("error", "停止录制失败");
        }
    }

    async function stopAllRecordings() {
        if (!state.adminMode) {
            notify("warning", "需要管理员模式");
            return;
        }
        try {
            const resp = await fetch("/api/ros/config/recordings/stop-all", {
                method: "POST",
                headers: {
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include"
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            notify("info", "已尝试停止所有录制任务");
            await Promise.all([loadActiveRecordings(), loadBagList()]);
        } catch (error) {
            console.error("停止全部录制失败", error);
            notify("error", "停止全部录制失败");
        }
    }

    // ==================== Bag 管理 ====================
    function bindBagEvents() {
        document.querySelector('[data-action="refresh-bags"]')?.addEventListener("click", loadBagList);
        document.querySelector('[data-action="start-playback"]')?.addEventListener("click", startPlayback);
        document.querySelector('[data-action="export-bag"]')?.addEventListener("click", exportBagData);
        document.querySelectorAll(".btn-group [data-export-format]").forEach(button => {
            button.addEventListener("click", () => {
                document.querySelectorAll(".btn-group [data-export-format]").forEach(btn => btn.classList.toggle("active", btn === button));
                state.selectedExportFormat = button.dataset.exportFormat;
            });
        });
    }

    async function loadBagList() {
        try {
            const resp = await fetch("/api/ros/config/bags", { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.bags = data.bags || [];
            renderBagTable();
            populateBagSelects();
        } catch (error) {
            console.error("加载 Bag 列表失败", error);
            notify("error", "加载 Bag 列表失败");
        }
    }

    function renderBagTable() {
        const tbody = document.getElementById("bags-table-body");
        if (!tbody) return;
        tbody.innerHTML = "";
        if (!state.bags.length) {
            tbody.innerHTML = `<tr class="text-muted"><td colspan="5" class="text-center py-4">未找到 Bag 数据</td></tr>`;
            return;
        }
        state.bags.forEach(bag => {
            const topics = Array.isArray(bag.metadata?.topics_with_message_count) ? bag.metadata.topics_with_message_count : [];
            const topicSummary = topics.length
                ? topics.map(item => `<span class="topic-chip">${item.topic_metadata?.name || item.topic_metadata?.topic_name} (${item.message_count || 0})</span>`).join("")
                : `<span class="metadata-empty">--</span>`;
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${bag.name}</td>
                <td>${bag.size_text}</td>
                <td>${formatTimestamp(bag.created_at)}</td>
                <td>${topicSummary}</td>
                <td>
                    <div class="btn-group btn-group-sm" role="group">
                        <button class="btn btn-outline-primary" data-action="download-bag" data-name="${bag.name}">
                            <i class="bi bi-download"></i>
                        </button>
                        <button class="btn btn-outline-success admin-only" data-action="queue-export" data-name="${bag.name}">
                            <i class="bi bi-cloud-download"></i>
                        </button>
                        <button class="btn btn-outline-danger admin-only" data-action="delete-bag" data-name="${bag.name}">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
        });
        updateAdminUI();
        tbody.querySelectorAll('[data-action="download-bag"]').forEach(btn => {
            btn.addEventListener("click", () => downloadBagArchive(btn.dataset.name));
        });
        tbody.querySelectorAll('[data-action="delete-bag"]').forEach(btn => {
            btn.addEventListener("click", () => deleteBag(btn.dataset.name));
        });
        tbody.querySelectorAll('[data-action="queue-export"]').forEach(btn => {
            btn.addEventListener("click", () => {
                document.getElementById("export-bag-select").value = btn.dataset.name;
                notify("info", `已选择 ${btn.dataset.name} 导出`);
            });
        });
    }

    function populateBagSelects() {
        const playbackSelect = document.getElementById("playback-bag-select");
        const exportSelect = document.getElementById("export-bag-select");
        if (playbackSelect) {
            playbackSelect.innerHTML = `<option value="">选择 Bag 文件</option>`;
        }
        if (exportSelect) {
            exportSelect.innerHTML = `<option value="">选择 Bag 文件</option>`;
        }
        state.bags.forEach(bag => {
            if (playbackSelect) {
                const option = document.createElement("option");
                option.value = bag.name;
                option.textContent = `${bag.name} (${bag.size_text})`;
                playbackSelect.appendChild(option);
            }
            if (exportSelect) {
                const option = document.createElement("option");
                option.value = bag.name;
                option.textContent = `${bag.name} (${bag.size_text})`;
                exportSelect.appendChild(option);
            }
        });
    }

    function downloadBagArchive(bagName) {
        if (!bagName) return;
        if (!state.adminMode) {
            notify("warning", "下载 Bag 需要管理员模式");
            return;
        }
        window.open(`/api/ros/config/bags/${encodeURIComponent(bagName)}/download`, "_blank");
    }

    async function deleteBag(bagName) {
        if (!bagName) return;
        if (!state.adminMode) {
            notify("warning", "需要管理员模式");
            return;
        }
        try {
            const resp = await fetch(`/api/ros/config/bags/${encodeURIComponent(bagName)}`, {
                method: "DELETE",
                headers: { "X-CSRF-Token": getCsrfToken() },
                credentials: "include"
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            notify("success", `已删除 ${bagName}`);
            await loadBagList();
        } catch (error) {
            console.error("删除 Bag 失败", error);
            notify("error", "删除 Bag 失败");
        }
    }

    async function startPlayback() {
        if (!state.adminMode) {
            notify("warning", "需要管理员模式");
            return;
        }
        const bagSelect = document.getElementById("playback-bag-select");
        const rateInput = document.getElementById("playback-rate");
        if (!bagSelect?.value) {
            notify("warning", "请选择 Bag 文件");
            return;
        }
        const payload = {
            bag_name: bagSelect.value,
            rate: rateInput?.value ? Number(rateInput.value) : 1.0,
            loop: document.getElementById("playback-loop")?.checked ?? false,
        };
        const topicsInput = document.getElementById("playback-topics");
        if (topicsInput?.value) {
            payload.topics = topicsInput.value.split(",").map(t => t.trim()).filter(Boolean);
        }
        try {
            const resp = await fetch("/api/ros/config/playback/start", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include",
                body: JSON.stringify(payload)
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            notify("success", "回放任务已启动");
            await loadPlaybacks();
        } catch (error) {
            console.error("启动回放失败", error);
            notify("error", "启动回放失败");
        }
    }

    async function loadPlaybacks() {
        try {
            const resp = await fetch("/api/ros/config/playback/active", { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.playbacks = data.playbacks || [];
            renderPlaybacks();
        } catch (error) {
            console.error("加载回放列表失败", error);
        }
    }

    function renderPlaybacks() {
        const tbody = document.getElementById("playback-table-body");
        if (!tbody) return;
        tbody.innerHTML = "";
        if (!state.playbacks.length) {
            tbody.innerHTML = `<tr class="text-muted"><td colspan="5" class="text-center py-3">暂无回放任务</td></tr>`;
            return;
        }
        state.playbacks.forEach(item => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${item.bag_name}</td>
                <td>${item.rate}x</td>
                <td>${item.loop ? "是" : "否"}</td>
                <td>${(item.topics || []).join(", ") || "-"}</td>
                <td class="admin-only">
                    <button class="btn btn-sm btn-outline-danger" data-action="stop-playback" data-id="${item.playback_id}">
                        <i class="bi bi-stop-circle"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(tr);
        });
        updateAdminUI();
        tbody.querySelectorAll('[data-action="stop-playback"]').forEach(btn => {
            btn.addEventListener("click", () => stopPlayback(btn.dataset.id));
        });
    }

    async function stopPlayback(playbackId) {
        if (!playbackId) return;
        try {
            const resp = await fetch("/api/ros/config/playback/stop", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include",
                body: JSON.stringify({ playback_id: playbackId })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            notify("success", "回放任务已停止");
            await loadPlaybacks();
        } catch (error) {
            console.error("停止回放失败", error);
            notify("error", "停止回放失败");
        }
    }

    async function exportBagData() {
        if (!state.adminMode) {
            notify("warning", "需要管理员模式");
            return;
        }
        const bagSelect = document.getElementById("export-bag-select");
        if (!bagSelect?.value) {
            notify("warning", "请选择 Bag 文件");
            return;
        }
        const format = state.selectedExportFormat || "json";
        const topicsInput = document.getElementById("export-topics");
        const startNs = document.getElementById("export-start-ns");
        const endNs = document.getElementById("export-end-ns");
        const payload = {
            format,
            topics: topicsInput?.value ? topicsInput.value.split(",").map(t => t.trim()).filter(Boolean) : undefined,
            start_time_ns: startNs?.value ? Number(startNs.value) : undefined,
            end_time_ns: endNs?.value ? Number(endNs.value) : undefined,
        };
        try {
            const resp = await fetch(`/api/ros/config/bags/${encodeURIComponent(bagSelect.value)}/export`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include",
                body: JSON.stringify(payload)
            });
            if (!resp.ok) {
                const error = await resp.json().catch(() => ({}));
                throw new Error(error?.detail?.message || `HTTP ${resp.status}`);
            }
            const blob = await resp.blob();
            const filename = extractFilename(resp) || `bag_export.${format}`;
            downloadBlob(blob, filename);
            notify("success", "导出任务完成");
        } catch (error) {
            console.error("导出 Bag 数据失败", error);
            notify("error", `导出失败：${error.message}`);
        }
    }

    // ==================== Lifecycle ====================
    function bindLifecycleEvents() {
        document.querySelector('[data-action="refresh-lifecycle"]')?.addEventListener("click", loadLifecycleNodes);
    }

    async function loadLifecycleNodes() {
        try {
            const resp = await fetch("/api/ros/config/lifecycle/nodes", { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.lifecycleNodes = data.nodes || [];
            renderLifecycleTable();
        } catch (error) {
            console.error("加载生命周期节点失败", error);
        }
    }

    function renderLifecycleTable() {
        const tbody = document.getElementById("lifecycle-table-body");
        if (!tbody) return;
        tbody.innerHTML = "";
        if (!state.lifecycleNodes.length) {
            tbody.innerHTML = `<tr class="text-muted"><td colspan="6" class="text-center py-4">暂无数据</td></tr>`;
            return;
        }
        state.lifecycleNodes.forEach(node => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${node.full_name}</td>
                <td>${node.namespace}</td>
                <td>${node.is_lifecycle ? '<span class="badge bg-success">支持</span>' : '<span class="badge bg-secondary">否</span>'}</td>
                <td>${node.current_state || "-"}</td>
                <td>${(node.available_states || []).join(", ") || "-"}</td>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-secondary" data-action="view-node-info" data-node="${node.full_name}">
                            <i class="bi bi-info-circle"></i>
                        </button>
                        <button class="btn btn-outline-secondary" data-action="view-node-logs" data-node="${node.full_name}">
                            <i class="bi bi-journal-text"></i>
                        </button>
                        <button class="btn btn-outline-danger admin-only" data-action="restart-node" data-node="${node.full_name}" ${node.is_lifecycle ? "" : "disabled"}>
                            <i class="bi bi-arrow-repeat"></i>
                        </button>
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
        });
        updateAdminUI();
        tbody.querySelectorAll('[data-action="restart-node"]').forEach(btn => {
            btn.addEventListener("click", () => restartNode(btn.dataset.node));
        });
        tbody.querySelectorAll('[data-action="view-node-logs"]').forEach(btn => {
            btn.addEventListener("click", () => viewNodeLogs(btn.dataset.node));
        });
        tbody.querySelectorAll('[data-action="view-node-info"]').forEach(btn => {
            btn.addEventListener("click", () => viewNodeInfo(btn.dataset.node));
        });
    }

    async function restartNode(node) {
        if (!state.adminMode) {
            notify("warning", "需要管理员模式");
            return;
        }
        try {
            const resp = await fetch("/api/ros/config/lifecycle/restart", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include",
                body: JSON.stringify({ node })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            notify("success", `已请求重启 ${node}`);
            await loadLifecycleNodes();
        } catch (error) {
            console.error("重启节点失败", error);
            notify("error", "重启节点失败");
        }
    }

    async function viewNodeLogs(node) {
        try {
            const resp = await fetch(`/api/ros/config/lifecycle/logs?node=${encodeURIComponent(node)}&limit=400`, { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            const logs = data.logs || [];
            if (!logs.length) {
                notify("info", "未找到日志文件");
                return;
            }
            const text = logs.map(item => `# ${item.path}\n${item.lines.join("\n")}`).join("\n\n");
            showModal(`节点日志 - ${node}`, text);
        } catch (error) {
            console.error("获取节点日志失败", error);
            notify("error", "获取节点日志失败");
        }
    }

    async function viewNodeInfo(node) {
        try {
            const resp = await fetch(`/api/ros/config/lifecycle/startup-info?node=${encodeURIComponent(node)}`, { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            showModal(`启动信息 - ${node}`, data.info_text || "无数据");
        } catch (error) {
            console.error("获取节点信息失败", error);
            notify("error", "获取节点信息失败");
        }
    }

    // ==================== Launch 管理 ====================
    function bindLaunchEvents() {
        document.querySelector('[data-action="refresh-launch-list"]')?.addEventListener("click", loadLaunchFiles);
        document.getElementById("launch-search")?.addEventListener("input", debounce(loadLaunchFiles, 400));
        document.getElementById("launch-include-global")?.addEventListener("change", () => {
            state.launchIncludeGlobal = document.getElementById("launch-include-global").checked;
            loadLaunchFiles();
        });
        document.querySelector('[data-action="start-launch"]')?.addEventListener("click", startLaunch);
    }

    async function loadLaunchFiles() {
        try {
            const search = document.getElementById("launch-search")?.value || "";
            const params = new URLSearchParams();
            if (search.trim()) params.append("search", search.trim());
            if (state.launchIncludeGlobal) params.append("include_global", "true");
            const resp = await fetch(`/api/ros/config/launch/files?${params.toString()}`, { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.launchFiles = data.files || [];
            renderLaunchFiles();
        } catch (error) {
            console.error("加载 Launch 文件失败", error);
        }
    }

    function renderLaunchFiles() {
        const tbody = document.getElementById("launch-files-body");
        if (!tbody) return;
        tbody.innerHTML = "";
        if (!state.launchFiles.length) {
            tbody.innerHTML = `<tr class="text-muted"><td colspan="3" class="text-center py-4">未找到 Launch 文件</td></tr>`;
            return;
        }
        state.launchFiles.forEach(file => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${file.name}</td>
                <td class="text-truncate" title="${file.path}">${file.path}</td>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-secondary" data-action="preview-launch" data-path="${file.path}">
                            <i class="bi bi-eye"></i>
                        </button>
                        <button class="btn btn-outline-primary admin-only" data-action="prefill-launch" data-path="${file.path}">
                            <i class="bi bi-clipboard-plus"></i>
                        </button>
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
        });
        updateAdminUI();
        tbody.querySelectorAll('[data-action="preview-launch"]').forEach(btn => {
            btn.addEventListener("click", () => previewLaunchArgs(btn.dataset.path));
        });
        tbody.querySelectorAll('[data-action="prefill-launch"]').forEach(btn => {
            btn.addEventListener("click", () => prefillLaunchForm(btn.dataset.path));
        });
    }

    async function previewLaunchArgs(path) {
        if (!path) return;
        try {
            const resp = await fetch("/api/ros/config/launch/preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "include",
                body: JSON.stringify({ package: null, launch_file: path })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            showModal(`Launch 参数 - ${path}`, data.output || "未提供参数信息");
        } catch (error) {
            console.error("预览 launch 参数失败", error);
            notify("error", "预览 launch 参数失败");
        }
    }

    function prefillLaunchForm(path) {
        if (!path) return;
        document.getElementById("launch-package").value = "";
        document.getElementById("launch-file").value = path;
        notify("info", "已填充 Launch 表单");
    }

    async function startLaunch() {
        if (!state.adminMode) {
            notify("warning", "需要管理员模式");
            return;
        }
        const packageInput = document.getElementById("launch-package");
        const fileInput = document.getElementById("launch-file");
        if (!fileInput?.value) {
            notify("warning", "请填写 Launch 文件");
            return;
        }
        const parameters = parseKeyValueString(document.getElementById("launch-parameters")?.value);
        const extraArgs = parseListString(document.getElementById("launch-extra-args")?.value);
        try {
            const resp = await fetch("/api/ros/config/launch/start", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include",
                body: JSON.stringify({
                    package: packageInput?.value || null,
                    launch_file: fileInput.value,
                    parameters,
                    additional_args: extraArgs,
                })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            notify("success", "Launch 任务已启动");
            await loadActiveLaunches();
        } catch (error) {
            console.error("启动 Launch 失败", error);
            notify("error", "启动 Launch 失败");
        }
    }

    async function loadActiveLaunches() {
        try {
            const resp = await fetch("/api/ros/config/launch/active", { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            state.activeLaunches = data.launches || [];
            renderActiveLaunches();
        } catch (error) {
            console.error("加载活动 Launch 失败", error);
        }
    }

    function renderActiveLaunches() {
        const tbody = document.getElementById("launch-active-body");
        if (!tbody) return;
        tbody.innerHTML = "";
        if (!state.activeLaunches.length) {
            tbody.innerHTML = `<tr class="text-muted"><td colspan="4" class="text-center py-4">暂无运行中的 Launch</td></tr>`;
            return;
        }
        state.activeLaunches.forEach(item => {
            const tr = document.createElement("tr");
            const command = Array.isArray(item.command) ? item.command.join(" ") : "";
            tr.innerHTML = `
                <td>${item.launch_id}</td>
                <td><code class="command-preview">${command}</code></td>
                <td>${new Date(item.start_time * 1000).toLocaleString()}</td>
                <td class="admin-only">
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-secondary" data-action="launch-logs" data-id="${item.launch_id}">
                            <i class="bi bi-file-earmark-text"></i>
                        </button>
                        <button class="btn btn-outline-danger" data-action="stop-launch" data-id="${item.launch_id}">
                            <i class="bi bi-stop-circle"></i>
                        </button>
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
        });
        updateAdminUI();
        tbody.querySelectorAll('[data-action="stop-launch"]').forEach(btn => {
            btn.addEventListener("click", () => stopLaunch(btn.dataset.id));
        });
        tbody.querySelectorAll('[data-action="launch-logs"]').forEach(btn => {
            btn.addEventListener("click", () => viewLaunchLogs(btn.dataset.id));
        });
    }

    async function stopLaunch(launchId) {
        if (!launchId) return;
        try {
            const resp = await fetch("/api/ros/config/launch/stop", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRF-Token": getCsrfToken()
                },
                credentials: "include",
                body: JSON.stringify({ launch_id: launchId })
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            notify("success", "已停止 Launch 任务");
            await loadActiveLaunches();
        } catch (error) {
            console.error("停止 Launch 失败", error);
            notify("error", "停止 Launch 失败");
        }
    }

    async function viewLaunchLogs(launchId) {
        try {
            const resp = await fetch(`/api/ros/config/launch/logs?launch_id=${encodeURIComponent(launchId)}&tail=400`, { credentials: "include" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            const stdout = (data.stdout || []).join("\n");
            const stderr = (data.stderr || []).join("\n");
            const content = `# STDOUT (${data.stdout_path})\n${stdout}\n\n# STDERR (${data.stderr_path})\n${stderr}`;
            showModal(`Launch 日志 - ${launchId}`, content);
        } catch (error) {
            console.error("获取 Launch 日志失败", error);
            notify("error", "获取 Launch 日志失败");
        }
    }

    // ==================== Util helpers ====================
    function parseKeyValueString(text) {
        if (!text) return null;
        const pairs = text.split(/\s+/).filter(Boolean);
        if (!pairs.length) return null;
        const result = {};
        pairs.forEach(pair => {
            const [key, value] = pair.split(":=");
            if (key && value !== undefined) {
                result[key.trim()] = value.trim();
            }
        });
        return Object.keys(result).length ? result : null;
    }

    function parseListString(text) {
        if (!text) return null;
        const parts = text.split(/\s+/).map(part => part.trim()).filter(Boolean);
        return parts.length ? parts : null;
    }

    function debounce(fn, delay) {
        let timer;
        return function debounced(...args) {
            clearTimeout(timer);
            timer = setTimeout(() => fn.apply(this, args), delay);
        };
    }
})();
