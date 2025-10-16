(() => {
    'use strict';

    const API_BASE = '/api/ros';
    const TOPIC_POLL_INTERVAL_MS = 1000;
    const MESSAGE_CACHE_LIMIT = 50;

    const state = {
        moduleActive: false,
        currentTab: 'topic-monitor',
        viewMode: 'single',
        topics: [],
        monitoredTopics: new Map(), // maps topic name to its monitoring metadata
        analysisCharts: {
            freq: null,
            size: null,
        },
        services: [],
        selectedService: null,
        callHistory: [],
        messageTypes: [],
        selectedType: null,
        typeFilter: 'all',
    };

    const elements = {};

    window.moduleInit = function moduleInit() {
        state.moduleActive = true;
        cacheDom();
        bindEvents();
        switchTab('topic-monitor');
    };

    window.moduleCleanup = function moduleCleanup() {
        state.moduleActive = false;

        unbindEvents();
        stopAllTopicMonitors();
        destroyCharts();

        state.currentTab = 'topic-monitor';
        state.selectedService = null;
        state.callHistory = [];
        state.selectedType = null;

        resetPlaceholders();
    };

    function cacheDom() {
        const $module = $('.ros-comm-module');

        elements.$commTabs = $module.find('.comm-tab');
        elements.$commPanels = $module.find('.comm-panel');

        elements.$btnRefreshTopics = $module.find('#btn-refresh-topics');
        elements.$topicSearch = $module.find('#topic-search');
        elements.$topicsList = $module.find('#topics-list');
        elements.$viewModeSelect = $module.find('#view-mode-select');
        elements.$monitorGrid = $module.find('#monitor-grid');

        elements.$analysisTopicSelect = $module.find('#analysis-topic-select');
        elements.$analysisTimerange = $module.find('#analysis-timerange');
        elements.$btnStartAnalysis = $module.find('#btn-start-analysis');
        elements.$statAvgFreq = $module.find('#stat-avg-freq');
        elements.$statAvgSize = $module.find('#stat-avg-size');
        elements.$statMsgCount = $module.find('#stat-msg-count');
        elements.$statMaxFreq = $module.find('#stat-max-freq');
        elements.$freqChart = $module.find('#freq-chart');
        elements.$sizeChart = $module.find('#size-chart');

        elements.$btnRefreshServices = $module.find('#btn-refresh-services');
        elements.$serviceSearch = $module.find('#service-search');
        elements.$servicesList = $module.find('#services-list');
        elements.$serviceInfo = $module.find('#service-info');
        elements.$selectedServiceName = $module.find('#selected-service-name');
        elements.$selectedServiceType = $module.find('#selected-service-type');
        elements.$requestEditorSection = $module.find('#request-editor-section');
        elements.$requestParams = $module.find('#request-params');
        elements.$btnFormatRequest = $module.find('#btn-format-request');
        elements.$btnClearRequest = $module.find('#btn-clear-request');
        elements.$btnCallService = $module.find('#btn-call-service');
        elements.$responseViewer = $module.find('#response-viewer');
        elements.$responseStatus = $module.find('#response-status');
        elements.$responseOutput = $module.find('#response-output');
        elements.$callHistorySection = $module.find('#call-history-section');
        elements.$callHistoryList = $module.find('#call-history-list');
        elements.$servicePlaceholder = $module.find('#service-placeholder');

        elements.$btnRefreshTypes = $module.find('#btn-refresh-types');
        elements.$typesSearch = $module.find('#types-search');
        elements.$typesList = $module.find('#types-list');
        elements.$filterBtns = $module.find('.filter-btn');
        elements.$typesPlaceholder = $module.find('#types-placeholder');
        elements.$typeInfo = $module.find('#type-info');
        elements.$selectedTypeName = $module.find('#selected-type-name');
        elements.$selectedTypePackage = $module.find('#selected-type-package');
        elements.$selectedTypeUsage = $module.find('#selected-type-usage');
        elements.$typeDefinitionSection = $module.find('#type-definition-section');
        elements.$typeDefinition = $module.find('#type-definition');
        elements.$btnCopyDefinition = $module.find('#btn-copy-definition');
        elements.$typeFieldsSection = $module.find('#type-fields-section');
        elements.$fieldsTable = $module.find('#fields-table tbody');
    }

    function bindEvents() {
        elements.$commTabs.on('click', handleTabClick);

        elements.$btnRefreshTopics.on('click', loadTopics);
        elements.$topicSearch.on('input', filterTopics);
        elements.$viewModeSelect.on('change', handleViewModeChange);
        elements.$topicsList.on('click', '.topic-card', handleTopicClick);

        elements.$btnStartAnalysis.on('click', startAnalysis);

        elements.$btnRefreshServices.on('click', loadServices);
        elements.$serviceSearch.on('input', filterServices);
        elements.$servicesList.on('click', '.service-card', handleServiceSelect);
        elements.$btnFormatRequest.on('click', formatRequestJson);
        elements.$btnClearRequest.on('click', clearRequestJson);
        elements.$btnCallService.on('click', callService);
        elements.$callHistoryList.on('click', '.history-item', handleHistoryReplay);

        elements.$btnRefreshTypes.on('click', loadMessageTypes);
        elements.$typesSearch.on('input', filterTypes);
        elements.$filterBtns.on('click', handleTypeFilterClick);
        elements.$typesList.on('click', '.type-card', handleTypeSelect);
        elements.$btnCopyDefinition.on('click', copyDefinition);
    }

    function unbindEvents() {
        elements.$commTabs.off('click');

        elements.$btnRefreshTopics.off('click');
        elements.$topicSearch.off('input');
        elements.$viewModeSelect.off('change');
        elements.$topicsList.off('click');

        elements.$btnStartAnalysis.off('click');

        elements.$btnRefreshServices.off('click');
        elements.$serviceSearch.off('input');
        elements.$servicesList.off('click');
        elements.$btnFormatRequest.off('click');
        elements.$btnClearRequest.off('click');
        elements.$btnCallService.off('click');
        elements.$callHistoryList.off('click');

        elements.$btnRefreshTypes.off('click');
        elements.$typesSearch.off('input');
        elements.$filterBtns.off('click');
        elements.$typesList.off('click');
        elements.$btnCopyDefinition.off('click');
    }

    function handleTabClick(event) {
        const $tab = $(event.currentTarget);
        const tabName = $tab.data('tab');
        if (tabName) {
            switchTab(tabName);
        }
    }

    function switchTab(tabName) {
        state.currentTab = tabName;

        elements.$commTabs.removeClass('active');
        elements.$commTabs.filter(`[data-tab="${tabName}"]`).addClass('active');

        elements.$commPanels.removeClass('active');
        $(`#panel-${tabName}`).addClass('active');

        if (!state.moduleActive) {
            return;
        }

        if (tabName === 'topic-monitor') {
            loadTopics();
        } else if (tabName === 'message-analysis') {
            loadTopicsForAnalysis();
        } else if (tabName === 'service-call') {
            loadServices();
        } else if (tabName === 'message-types') {
            loadMessageTypes();
        }
    }

    function stopAllTopicMonitors() {
        const topics = Array.from(state.monitoredTopics.keys());
        topics.forEach((topic) => stopMonitoringTopic(topic, true));
        state.monitoredTopics.clear();
        elements.$monitorGrid.empty();
        elements.$monitorGrid.append(buildMonitorPlaceholder());
    }

    function destroyCharts() {
        if (state.analysisCharts.freq) {
            state.analysisCharts.freq.destroy();
            state.analysisCharts.freq = null;
        }
        if (state.analysisCharts.size) {
            state.analysisCharts.size.destroy();
            state.analysisCharts.size = null;
        }
    }

    function resetPlaceholders() {
        elements.$serviceInfo.hide();
        elements.$requestEditorSection.hide();
        elements.$responseViewer.hide();
        elements.$callHistorySection.hide();
        elements.$servicePlaceholder.show();

        elements.$typeInfo.hide();
        elements.$typeDefinitionSection.hide();
        elements.$typeFieldsSection.hide();
        elements.$typesPlaceholder.show();
    }

    async function loadTopics() {
        try {
            showLoading(elements.$topicsList);

            const response = await fetch(`${API_BASE}/topics`, { credentials: 'include' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            if (!data.success || !Array.isArray(data.topics)) {
                throw new Error(data.message || '获取话题列表失败');
            }

            state.topics = data.topics;
            renderTopics();
            updateAnalysisTopicSelect();
        } catch (error) {
            console.error('加载话题列表失败:', error);
            showError(elements.$topicsList, '加载话题列表失败');
            if (window.toastr) {
                toastr.error('无法获取话题列表，请检查 ROS 环境', '错误');
            }
        }
    }

    function renderTopics(filteredTopics) {
        const topicsToRender = filteredTopics || state.topics;
        elements.$topicsList.empty();

        if (!topicsToRender || topicsToRender.length === 0) {
            showEmpty(elements.$topicsList, '暂无话题');
            return;
        }

        topicsToRender.forEach((topic) => {
            const isMonitored = state.monitoredTopics.has(topic.name);
            const $card = $(`
                <div class="topic-card ${isMonitored ? 'monitored' : ''}" data-topic="${escapeHtml(topic.name)}">
                    <div class="topic-header">
                        <div class="topic-name">${escapeHtml(topic.name)}</div>
                        ${isMonitored ? '<span class="topic-badge">监控中</span>' : ''}
                    </div>
                    <div class="topic-meta">
                        <div class="topic-meta-item">
                            <span class="topic-meta-label">类型:</span>
                            <span class="topic-meta-value">${escapeHtml(topic.type || '-')}</span>
                        </div>
                        <div class="topic-meta-item">
                            <span class="topic-meta-label">发布者:</span>
                            <span class="topic-meta-value">${topic.publishers ?? 0}</span>
                        </div>
                        <div class="topic-meta-item">
                            <span class="topic-meta-label">订阅者:</span>
                            <span class="topic-meta-value">${topic.subscribers ?? 0}</span>
                        </div>
                        <div class="topic-meta-item">
                            <span class="topic-meta-label">频率:</span>
                            <span class="topic-meta-value">${formatFrequency(topic.frequency)}</span>
                        </div>
                    </div>
                </div>
            `);
            elements.$topicsList.append($card);
        });
    }

    function filterTopics() {
        const keyword = (elements.$topicSearch.val() || '').toLowerCase();
        if (!keyword) {
            renderTopics();
            return;
        }
        const filtered = state.topics.filter((topic) => topic.name.toLowerCase().includes(keyword));
        renderTopics(filtered);
    }

    function handleTopicClick(event) {
        const $card = $(event.currentTarget);
        const topicName = $card.data('topic');

        elements.$topicsList.find('.topic-card').removeClass('active');
        $card.addClass('active');

        startMonitoringTopic(topicName);
    }

    function handleViewModeChange(event) {
        state.viewMode = $(event.target).val();
        elements.$monitorGrid.attr('data-mode', state.viewMode);
        renderMonitorGrid();
    }

    function startMonitoringTopic(topicName) {
        if (state.monitoredTopics.has(topicName)) {
            if (window.toastr) {
                toastr.info(`话题 ${topicName} 已在监控中`, '提示');
            }
            return;
        }

        enforceMonitorLimit();

        const monitor = {
            topicName,
            messages: [],
            frequency: 0,
            lastMessageTs: null,
            paused: false,
            pollHandle: null,
        };

        state.monitoredTopics.set(topicName, monitor);
        renderTopics();
        renderMonitorGrid();
        pollLatestMessage(topicName);
        monitor.pollHandle = window.setInterval(() => pollLatestMessage(topicName), TOPIC_POLL_INTERVAL_MS);

        if (window.toastr) {
            toastr.success(`已开始监控 ${topicName}`, '成功');
        }
    }

    function enforceMonitorLimit() {
        let maxWindows = 1;
        if (state.viewMode === 'grid-2') {
            maxWindows = 2;
        } else if (state.viewMode === 'grid-4') {
            maxWindows = 4;
        }

        while (state.monitoredTopics.size >= maxWindows) {
            const oldestTopic = state.monitoredTopics.keys().next().value;
            if (!oldestTopic) break;
            stopMonitoringTopic(oldestTopic, false);
        }
    }

    function stopMonitoringTopic(topicName, fromCleanup) {
        const monitor = state.monitoredTopics.get(topicName);
        if (!monitor) {
            return;
        }

        if (monitor.pollHandle) {
            window.clearInterval(monitor.pollHandle);
        }

        state.monitoredTopics.delete(topicName);

        if (!fromCleanup) {
            renderTopics();
            renderMonitorGrid();
        }
    }

    async function pollLatestMessage(topicName) {
        const monitor = state.monitoredTopics.get(topicName);
        if (!monitor || monitor.paused) {
            return;
        }

        try {
            const response = await fetch(`${API_BASE}/topics/${encodeURIComponent(topicName)}/messages?limit=1`, {
                credentials: 'include',
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            if (!data.success || !Array.isArray(data.messages) || data.messages.length === 0) {
                return;
            }
            addMessageToMonitor(topicName, data.messages[0]);
        } catch (error) {
            console.error(`获取话题 ${topicName} 消息失败:`, error);
        }
    }

    function addMessageToMonitor(topicName, message) {
        const monitor = state.monitoredTopics.get(topicName);
        if (!monitor) {
            return;
        }

        monitor.messages.unshift(message);
        if (monitor.messages.length > MESSAGE_CACHE_LIMIT) {
            monitor.messages.length = MESSAGE_CACHE_LIMIT;
        }

        const now = Date.now();
        if (monitor.lastMessageTs) {
            const deltaMs = now - monitor.lastMessageTs;
            if (deltaMs > 0) {
                monitor.frequency = 1000 / deltaMs;
            }
        }
        monitor.lastMessageTs = now;

        updateMonitorWindow(topicName);
    }

    function renderMonitorGrid() {
        elements.$monitorGrid.empty();
        elements.$monitorGrid.attr('data-mode', state.viewMode);

        if (state.monitoredTopics.size === 0) {
            elements.$monitorGrid.append(buildMonitorPlaceholder());
            return;
        }

        state.monitoredTopics.forEach((monitor) => {
            const windowId = `monitor-${monitor.topicName.replace(/[\\/ ]/g, '_')}`;
            const $window = $(`
                <div class="monitor-window" id="${windowId}">
                    <div class="window-header">
                        <div class="window-title">
                            <i class="bi bi-broadcast"></i>
                            <span>${escapeHtml(monitor.topicName)}</span>
                        </div>
                        <div class="window-actions">
                            <button class="window-btn btn-pause" data-topic="${escapeHtml(monitor.topicName)}" title="${monitor.paused ? '继续' : '暂停'}">
                                <i class="bi ${monitor.paused ? 'bi-play-fill' : 'bi-pause-fill'}"></i>
                            </button>
                            <button class="window-btn btn-clear" data-topic="${escapeHtml(monitor.topicName)}" title="清空">
                                <i class="bi bi-trash"></i>
                            </button>
                            <button class="window-btn btn-export" data-topic="${escapeHtml(monitor.topicName)}" title="导出最近消息">
                                <i class="bi bi-download"></i>
                            </button>
                            <button class="window-btn btn-close" data-topic="${escapeHtml(monitor.topicName)}" title="停止监控">
                                <i class="bi bi-x-lg"></i>
                            </button>
                        </div>
                    </div>
                    <div class="window-meta">
                        <div class="window-meta-item">
                            <span class="window-meta-label">频率:</span>
                            <span class="window-meta-value freq">${formatFrequency(monitor.frequency)}</span>
                        </div>
                        <div class="window-meta-item">
                            <span class="window-meta-label">缓存:</span>
                            <span class="window-meta-value count">${monitor.messages.length}</span>
                        </div>
                    </div>
                    <div class="window-body messages-container">
                        ${monitor.messages.length === 0 ? '<div class="empty-state"><p>暂未收到消息</p></div>' : ''}
                    </div>
                    <div class="window-footer">
                        <span class="footer-info">最多保留最近 50 条消息</span>
                    </div>
                </div>
            `);

            $window.find('.btn-pause').on('click', (event) => {
                event.stopPropagation();
                togglePause(monitor.topicName);
            });
            $window.find('.btn-clear').on('click', (event) => {
                event.stopPropagation();
                clearMessages(monitor.topicName);
            });
            $window.find('.btn-close').on('click', (event) => {
                event.stopPropagation();
                stopMonitoringTopic(monitor.topicName, false);
            });
            $window.find('.btn-export').on('click', (event) => {
                event.stopPropagation();
                exportMessages(monitor.topicName);
            });

            elements.$monitorGrid.append($window);

            if (monitor.messages.length > 0) {
                renderMessages(monitor.topicName);
            }
        });
    }

    function updateMonitorWindow(topicName) {
        const monitor = state.monitoredTopics.get(topicName);
        if (!monitor) {
            return;
        }

        const windowId = `monitor-${monitor.topicName.replace(/[\\/ ]/g, '_')}`;
        const $window = $(`#${windowId}`);
        if ($window.length === 0) {
            renderMonitorGrid();
            return;
        }

        $window.find('.window-meta-value.freq').text(formatFrequency(monitor.frequency));
        $window.find('.window-meta-value.count').text(monitor.messages.length);
        renderMessages(topicName);
    }

    function renderMessages(topicName) {
        const monitor = state.monitoredTopics.get(topicName);
        if (!monitor) {
            return;
        }

        const windowId = `monitor-${monitor.topicName.replace(/[\\/ ]/g, '_')}`;
        const $container = $(`#${windowId} .messages-container`);
        $container.empty();

        monitor.messages.forEach((msg, index) => {
            const timestamp = msg.timestamp || msg.header?.stamp || new Date().toISOString();
            const seq = msg.seq ?? msg.header?.seq ?? index;
            const payload = normaliseMessagePayload(msg);
            const $item = $(`
                <div class="message-item">
                    <div class="message-header">
                        <span class="message-timestamp">${formatTimestamp(timestamp)}</span>
                        <span class="message-seq">SEQ: ${escapeHtml(String(seq))}</span>
                    </div>
                    <pre class="message-content">${escapeHtml(payload)}</pre>
                </div>
            `);
            $container.append($item);
        });
    }

    function togglePause(topicName) {
        const monitor = state.monitoredTopics.get(topicName);
        if (!monitor) {
            return;
        }
        monitor.paused = !monitor.paused;
        const windowId = `monitor-${monitor.topicName.replace(/[\\/ ]/g, '_')}`;
        const $icon = $(`#${windowId} .btn-pause i`);
        if (monitor.paused) {
            $icon.removeClass('bi-pause-fill').addClass('bi-play-fill');
            if (window.toastr) {
                toastr.info(`已暂停 ${topicName}`, '提示');
            }
        } else {
            $icon.removeClass('bi-play-fill').addClass('bi-pause-fill');
            if (window.toastr) {
                toastr.info(`已恢复 ${topicName}`, '提示');
            }
        }
    }

    function clearMessages(topicName) {
        const monitor = state.monitoredTopics.get(topicName);
        if (!monitor) {
            return;
        }
        monitor.messages = [];
        monitor.frequency = 0;
        monitor.lastMessageTs = null;
        updateMonitorWindow(topicName);
        if (window.toastr) {
            toastr.success(`已清空 ${topicName} 的缓存`, '成功');
        }
    }

    function exportMessages(topicName) {
        const monitor = state.monitoredTopics.get(topicName);
        if (!monitor || monitor.messages.length === 0) {
            if (window.toastr) {
                toastr.warning('暂无可导出的消息', '提示');
            }
            return;
        }

        const blob = new Blob([JSON.stringify(monitor.messages, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${topicName.replace(/[\\/]/g, '_')}_messages.json`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    }

    function loadTopicsForAnalysis() {
        if (state.topics.length === 0) {
            loadTopics().finally(updateAnalysisTopicSelect);
        } else {
            updateAnalysisTopicSelect();
        }
    }

    function updateAnalysisTopicSelect() {
        elements.$analysisTopicSelect.empty();
        elements.$analysisTopicSelect.append('<option value="">选择要分析的话题...</option>');
        state.topics.forEach((topic) => {
            elements.$analysisTopicSelect.append(`<option value="${escapeHtml(topic.name)}">${escapeHtml(topic.name)}</option>`);
        });
    }

    async function startAnalysis() {
        const topicName = elements.$analysisTopicSelect.val();
        const timerange = elements.$analysisTimerange.val();

        if (!topicName) {
            if (window.toastr) {
                toastr.warning('请先选择话题', '提示');
            }
            return;
        }

        try {
            if (window.toastr) {
                toastr.info('正在计算统计数据...', '处理中');
            }

            const response = await fetch(`${API_BASE}/topics/${encodeURIComponent(topicName)}/analysis?timerange=${encodeURIComponent(timerange)}`, {
                method: 'POST',
                credentials: 'include',
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            if (!data.success || !data.analysis) {
                throw new Error(data.message || '获取分析数据失败');
            }

            renderAnalysisResults(data.analysis);
            if (window.toastr) {
                toastr.success('分析完成', '成功');
            }
        } catch (error) {
            console.error('分析话题失败:', error);
            if (window.toastr) {
                toastr.error('无法获取分析数据', '错误');
            }
        }
    }

    function renderAnalysisResults(analysis) {
        elements.$statAvgFreq.text(`${Number(analysis.avg_frequency || 0).toFixed(2)} Hz`);
        elements.$statAvgSize.text(formatBytes(analysis.avg_size || 0));
        elements.$statMsgCount.text(analysis.message_count ?? 0);
        elements.$statMaxFreq.text(`${Number(analysis.max_frequency || 0).toFixed(2)} Hz`);

        renderFrequencyChart(analysis.frequency_data || []);
        renderSizeChart(analysis.size_distribution || []);
    }

    function renderFrequencyChart(data) {
        if (!elements.$freqChart.length) {
            return;
        }
        if (state.analysisCharts.freq) {
            state.analysisCharts.freq.destroy();
        }
        const ctx = elements.$freqChart.get(0).getContext('2d');
        state.analysisCharts.freq = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.map((entry) => formatTimestamp(entry.timestamp)),
                datasets: [{
                    label: '频率 (Hz)',
                    data: data.map((entry) => entry.frequency ?? 0),
                    borderColor: '#0ea5e9',
                    backgroundColor: 'rgba(14, 165, 233, 0.1)',
                    fill: true,
                    tension: 0.4,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: '频率 (Hz)' },
                    },
                },
            },
        });
    }

    function renderSizeChart(data) {
        if (!elements.$sizeChart.length) {
            return;
        }
        if (state.analysisCharts.size) {
            state.analysisCharts.size.destroy();
        }
        const ctx = elements.$sizeChart.get(0).getContext('2d');
        state.analysisCharts.size = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.map((entry) => entry.range || '-'),
                datasets: [{
                    label: '消息数量',
                    data: data.map((entry) => entry.count ?? 0),
                    backgroundColor: '#10b981',
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: '消息数量' },
                    },
                },
            },
        });
    }

    async function loadServices() {
        try {
            showLoading(elements.$servicesList);
            const response = await fetch(`${API_BASE}/services`, { credentials: 'include' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            if (!data.success || !Array.isArray(data.services)) {
                throw new Error(data.message || '获取服务列表失败');
            }
            state.services = data.services;
            renderServices();
        } catch (error) {
            console.error('加载服务列表失败:', error);
            showError(elements.$servicesList, '加载服务失败');
            if (window.toastr) {
                toastr.error('无法获取服务列表，请检查 ROS 环境', '错误');
            }
        }
    }

    function renderServices(filteredServices) {
        const servicesToRender = filteredServices || state.services;
        elements.$servicesList.empty();

        if (!servicesToRender || servicesToRender.length === 0) {
            showEmpty(elements.$servicesList, '暂无服务');
            return;
        }

        servicesToRender.forEach((service) => {
            const $card = $(`
                <div class="service-card" data-service="${escapeHtml(service.name)}">
                    <div class="service-card-name">${escapeHtml(service.name)}</div>
                    <div class="service-card-type">${escapeHtml(service.type || '-')}</div>
                </div>
            `);
            elements.$servicesList.append($card);
        });
    }

    function filterServices() {
        const keyword = (elements.$serviceSearch.val() || '').toLowerCase();
        if (!keyword) {
            renderServices();
            return;
        }
        const filtered = state.services.filter((service) => service.name.toLowerCase().includes(keyword));
        renderServices(filtered);
    }

    function handleServiceSelect(event) {
        const $card = $(event.currentTarget);
        const serviceName = $card.data('service');

        elements.$servicesList.find('.service-card').removeClass('active');
        $card.addClass('active');

        selectService(serviceName);
    }

    function selectService(serviceName) {
        const service = state.services.find((entry) => entry.name === serviceName);
        if (!service) {
            return;
        }

        state.selectedService = service;
        elements.$servicePlaceholder.hide();
        elements.$serviceInfo.show();
        elements.$requestEditorSection.show();
        elements.$responseViewer.hide();
        elements.$callHistorySection.show();

        elements.$selectedServiceName.text(service.name);
        elements.$selectedServiceType.text(service.type || '-');
        elements.$requestParams.val('');

        renderCallHistory(service.name);
    }

    function formatRequestJson() {
        const raw = elements.$requestParams.val();
        if (!raw || !raw.trim()) {
            if (window.toastr) {
                toastr.warning('没有可格式化的 JSON 内容', '提示');
            }
            return;
        }
        try {
            const formatted = JSON.stringify(JSON.parse(raw), null, 2);
            elements.$requestParams.val(formatted);
            if (window.toastr) {
                toastr.success('JSON 已格式化', '成功');
            }
        } catch (error) {
            if (window.toastr) {
                toastr.error('JSON 格式不正确', '错误');
            }
        }
    }

    function clearRequestJson() {
        elements.$requestParams.val('');
    }

    async function callService() {
        if (!state.selectedService) {
            if (window.toastr) {
                toastr.warning('请先选择服务', '提示');
            }
            return;
        }

        let params = {};
        const raw = elements.$requestParams.val().trim();
        if (raw) {
            try {
                params = JSON.parse(raw);
            } catch (error) {
                if (window.toastr) {
                    toastr.error('请求参数不是有效的 JSON', '错误');
                }
                return;
            }
        }

        try {
            const start = Date.now();
            if (window.toastr) {
                toastr.info(`正在调用 ${state.selectedService.name}`, '执行中');
            }

            const response = await fetch(`${API_BASE}/services/${encodeURIComponent(state.selectedService.name)}/call`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ params }),
            });
            const elapsed = Date.now() - start;
            const data = await response.json();

            elements.$responseViewer.show();
            const ok = response.ok && data.success;
            elements.$responseStatus
                .removeClass('success error')
                .addClass(ok ? 'success' : 'error')
                .text(ok ? '成功' : '失败');
            elements.$responseOutput.text(JSON.stringify(data.result ?? data, null, 2));

            addCallHistory({
                service: state.selectedService.name,
                params,
                success: ok,
                result: data.result ?? data,
                duration: elapsed,
                timestamp: new Date().toISOString(),
            });

            if (window.toastr) {
                if (ok) {
                    toastr.success(`调用成功 (${elapsed} ms)`, '成功');
                } else {
                    toastr.error(data.message || '服务调用失败', '错误');
                }
            }
        } catch (error) {
            console.error('调用服务失败:', error);
            elements.$responseViewer.show();
            elements.$responseStatus.removeClass('success').addClass('error').text('异常');
            elements.$responseOutput.text(error.message || String(error));
            if (window.toastr) {
                toastr.error('服务调用出现异常', '错误');
            }
        }
    }

    function addCallHistory(record) {
        state.callHistory.unshift(record);
        if (state.callHistory.length > 20) {
            state.callHistory.length = 20;
        }
        renderCallHistory(record.service);
    }

    function renderCallHistory(serviceName) {
        const history = state.callHistory.filter((entry) => entry.service === serviceName);

        elements.$callHistoryList.empty();
        if (history.length === 0) {
            elements.$callHistoryList.html('<div class="empty-state"><p>暂无调用记录</p></div>');
            return;
        }

        history.forEach((entry) => {
            const $item = $(`
                <div class="history-item">
                    <div class="history-item-header">
                        <span class="history-time">${formatTimestamp(entry.timestamp)}</span>
                        <span class="history-duration">${entry.duration} ms</span>
                    </div>
                    <div class="history-item-body">${escapeHtml(JSON.stringify(entry.params))}</div>
                </div>
            `);

            $item.on('click', () => {
                elements.$requestParams.val(JSON.stringify(entry.params, null, 2));
            });

            elements.$callHistoryList.append($item);
        });
    }

    function handleHistoryReplay(event) {
        event.preventDefault();
    }

    async function loadMessageTypes() {
        try {
            showLoading(elements.$typesList);
            const response = await fetch(`${API_BASE}/message-types`, { credentials: 'include' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            if (!data.success || !Array.isArray(data.types)) {
                throw new Error(data.message || '获取消息类型失败');
            }
            state.messageTypes = data.types;
            updateTypeCounts();
            renderMessageTypes();
        } catch (error) {
            console.error('加载消息类型失败:', error);
            showError(elements.$typesList, '加载消息类型失败');
            if (window.toastr) {
                toastr.error('无法获取消息类型列表', '错误');
            }
        }
    }

    function updateTypeCounts() {
        const total = state.messageTypes.length;
        const custom = state.messageTypes.filter((type) => type.is_custom).length;
        const standard = total - custom;

        $('#count-all').text(total);
        $('#count-custom').text(custom);
        $('#count-standard').text(standard);
    }

    function renderMessageTypes(filteredTypes) {
        const typesToRender = filteredTypes || state.messageTypes;
        elements.$typesList.empty();

        if (!typesToRender || typesToRender.length === 0) {
            showEmpty(elements.$typesList, '暂无消息类型');
            return;
        }

        typesToRender.forEach((type) => {
            const $card = $(`
                <div class="type-card ${type.is_custom ? 'custom' : ''}" data-type="${escapeHtml(type.name)}">
                    <div class="type-card-header">
                        <div class="type-card-name">${escapeHtml(type.name)}</div>
                        ${type.is_custom ? '<span class="type-card-badge">自定义</span>' : ''}
                    </div>
                    <div class="type-card-package">${escapeHtml(type.package || '-')}</div>
                </div>
            `);
            elements.$typesList.append($card);
        });
    }

    function filterTypes() {
        const keyword = (elements.$typesSearch.val() || '').toLowerCase();
        let filtered = [...state.messageTypes];

        if (state.typeFilter === 'custom') {
            filtered = filtered.filter((type) => type.is_custom);
        } else if (state.typeFilter === 'standard') {
            filtered = filtered.filter((type) => !type.is_custom);
        }

        if (keyword) {
            filtered = filtered.filter((type) => type.name.toLowerCase().includes(keyword));
        }

        renderMessageTypes(filtered);
    }

    function handleTypeFilterClick(event) {
        const $button = $(event.currentTarget);
        state.typeFilter = $button.data('filter');
        elements.$filterBtns.removeClass('active');
        $button.addClass('active');
        filterTypes();
    }

    function handleTypeSelect(event) {
        const $card = $(event.currentTarget);
        const typeName = $card.data('type');
        elements.$typesList.find('.type-card').removeClass('active');
        $card.addClass('active');
        loadTypeDefinition(typeName);
    }

    async function loadTypeDefinition(typeName) {
        try {
            const response = await fetch(`${API_BASE}/message-types/${encodeURIComponent(typeName)}`, { credentials: 'include' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            if (!data.success || !data.type) {
                throw new Error(data.message || '获取消息定义失败');
            }
            state.selectedType = data.type;
            renderTypeDefinition(data.type);
        } catch (error) {
            console.error('加载消息定义失败:', error);
            if (window.toastr) {
                toastr.error('无法加载消息定义', '错误');
            }
        }
    }

    function renderTypeDefinition(type) {
        elements.$typesPlaceholder.hide();
        elements.$typeInfo.show();
        elements.$typeDefinitionSection.show();

        elements.$selectedTypeName.text(type.name);
        elements.$selectedTypePackage.text(type.package || '-');
        elements.$selectedTypeUsage.text(`使用次数: ${type.usage_count ?? 0}`);
        elements.$typeDefinition.text(type.definition || '# 未找到定义');

        if (Array.isArray(type.fields) && type.fields.length > 0) {
            elements.$typeFieldsSection.show();
            elements.$fieldsTable.empty();
            type.fields.forEach((field) => {
                const $row = $(`
                    <tr>
                        <td class="field-name">${escapeHtml(field.name)}</td>
                        <td class="field-type">${escapeHtml(field.type)}</td>
                        <td class="field-desc">${escapeHtml(field.description || '-')}</td>
                    </tr>
                `);
                elements.$fieldsTable.append($row);
            });
        } else {
            elements.$typeFieldsSection.hide();
            elements.$fieldsTable.empty();
        }
    }

    function copyDefinition() {
        if (!state.selectedType || !state.selectedType.definition) {
            if (window.toastr) {
                toastr.warning('当前没有可复制的定义', '提示');
            }
            return;
        }
        navigator.clipboard.writeText(state.selectedType.definition)
            .then(() => {
                if (window.toastr) {
                    toastr.success('已复制到剪贴板', '成功');
                }
            })
            .catch((error) => {
                console.error('复制失败:', error);
                if (window.toastr) {
                    toastr.error('复制失败，请检查浏览器权限', '错误');
                }
            });
    }

    function buildMonitorPlaceholder() {
        return $(`
            <div class="monitor-placeholder">
                <i class="bi bi-arrow-left-circle"></i>
                <p>从左侧选择话题开始监控</p>
            </div>
        `);
    }

    function showLoading($container) {
        $container.html(`
            <div class="loading-state">
                <i class="bi bi-arrow-clockwise spinner"></i>
                <span>加载中...</span>
            </div>
        `);
    }

    function showEmpty($container, message) {
        $container.html(`
            <div class="empty-state">
                <i class="bi bi-inbox"></i>
                <p>${escapeHtml(message || '暂无数据')}</p>
            </div>
        `);
    }

    function showError($container, message) {
        $container.html(`
            <div class="error-state">
                <i class="bi bi-exclamation-octagon"></i>
                <p>${escapeHtml(message || '加载失败')}</p>
            </div>
        `);
    }

    function formatTimestamp(timestamp) {
        try {
            const date = typeof timestamp === 'string'
                ? new Date(timestamp)
                : new Date((timestamp.sec || timestamp.seconds || 0) * 1000 + Math.floor((timestamp.nanosec || timestamp.nanoseconds || 0) / 1e6));
            if (Number.isNaN(date.getTime())) {
                return '--:--:--';
            }
            return date.toLocaleTimeString('zh-CN', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                fractionalSecondDigits: 3,
            });
        } catch (error) {
            return '--:--:--';
        }
    }

    function formatBytes(bytes) {
        const value = Number(bytes) || 0;
        if (value === 0) {
            return '0 B';
        }
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
        const size = value / (1024 ** exponent);
        return `${size.toFixed(1)} ${units[exponent]}`;
    }

    function formatFrequency(value) {
        if (!value || Number.isNaN(value) || !Number.isFinite(value)) {
            return '-';
        }
        return `${value.toFixed(1)} Hz`;
    }

    function normaliseMessagePayload(message) {
        if (message === null || message === undefined) {
            return 'null';
        }
        if (typeof message === 'string') {
            return message;
        }
        if (typeof message === 'number' || typeof message === 'boolean') {
            return String(message);
        }
        if (typeof message === 'object') {
            return JSON.stringify(message, null, 2);
        }
        return String(message);
    }

    function escapeHtml(value) {
        if (value === null || value === undefined) {
            return '';
        }
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
})();
