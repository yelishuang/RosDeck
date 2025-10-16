/**
 * 存储管理模块逻辑
 */

(() => {
    const POLL_INTERVAL = 5000;

    const state = {
        isAdmin: false,
        charts: {
            usageRing: null,
            ioLine: null,
            historyLine: null,
            overviewLine: null,
            trendUsage: null,
            trendIo: null
        },
        pollTimer: null,
        countdowns: new Map(),
        partitions: [],
        lastSummary: null,
        lastReportInfo: null
    };

    let $module = null;
    let adminModeHandler = null;
    let apiReadyPromise = null;

    // ==================== Module lifecycle ====================
    function moduleInit() {
        ensureApiReady()
            .then(() => initializeModule())
            .catch(error => {
                console.error('存储管理模块初始化失败:', error);
                notify('error', '加载存储模块依赖失败');
            });
    }

    async function initializeModule() {
        console.log('存储管理模块初始化');
        $module = $('.storage-module');
        state.isAdmin = $('.admin-mode-toggle').hasClass('active');

        setupCharts();
        bindEvents();
        updateAdminUI();

        adminModeHandler = handleAdminModeChange.bind(null);
        window.addEventListener('rosdeck:admin-mode-change', adminModeHandler);

        try {
            await loadStorageData();
        } catch (error) {
            console.error('初始加载失败:', error);
        }

        if (!$module) {
            return;
        }

        startPolling();

        if (state.isAdmin && $module) {
            refreshOperationLog();
        }
    }

    function moduleCleanup() {
        console.log('存储管理模块卸载');
        stopPolling();
        clearCountdowns();
        destroyCharts();
        if (adminModeHandler) {
            window.removeEventListener('rosdeck:admin-mode-change', adminModeHandler);
            adminModeHandler = null;
        }
        $module = null;
    }

    window.moduleInit = moduleInit;
    window.moduleCleanup = moduleCleanup;

    function ensureApiReady() {
        if (window.StorageApi) {
            return Promise.resolve();
        }
        if (!apiReadyPromise) {
            apiReadyPromise = new Promise((resolve, reject) => {
                $.ajax({
                    url: 'modules/storage/api.js',
                    dataType: 'script',
                    cache: true,
                    success: resolve,
                    error: (_, __, err) => reject(err || new Error('加载 StorageApi 失败'))
                });
            });
        }
        return apiReadyPromise;
    }

    // ==================== Event binding ====================
    function bindEvents() {
        if (!$module) {
            return;
        }

        $module.find('.detail-card .nav-link').on('click', function (event) {
            event.preventDefault();
            const targetId = $(this).data('target');
            if (!targetId) {
                return;
            }
            switchTab($(this), targetId);
        });

        $module.find('[data-action="export-report"]').on('click', onExportReport);
        $module.find('[data-action="cleanup-dry-run"]').on('click', onCleanupDryRun);
        attachCountdownButton($module.find('[data-action="cleanup-run"]'), onCleanupExecute);

        attachCountdownButton($module.find('#storage-mount-form .countdown-btn'), onMountSubmit);
        attachCountdownButton($module.find('#storage-partition-form .countdown-btn'), onPartitionSubmit);
        attachCountdownButton($module.find('#storage-smart-form .countdown-btn'), onSmartSubmit);

        $module.find('#storage-smart-refresh').on('click', onSmartReport);
    }

    function switchTab($button, targetId) {
        const $tabs = $module.find('.detail-card .nav-link');
        const $panes = $module.find('.tab-pane');
        $tabs.removeClass('active');
        $button.addClass('active');
        $panes.removeClass('show active');
        $module.find(targetId).addClass('show active');

        if (targetId === '#tab-content-trends') {
            scheduleTrendChartResize();
        }
    }

    // ==================== Data loading and polling ====================
    async function loadStorageData() {
        if (!$module) {
            return;
        }
        try {
            const [summary, partitionResp] = await Promise.all([
                StorageApi.summary(),
                StorageApi.partitions()
            ]);

            const partitions = Array.isArray(partitionResp?.partitions) ? partitionResp.partitions : [];
            state.partitions = partitions;
            state.lastSummary = summary;

            updateSummaryView(summary);
            updatePartitionsView(partitions);
            updateOverviewMetrics(summary, partitions);
        } catch (error) {
            console.error('加载存储信息失败:', error);
            notify('error', error.message || '加载存储信息失败');
        }
    }

    function startPolling() {
        stopPolling();
        state.pollTimer = setInterval(loadStorageData, POLL_INTERVAL);
    }

    function stopPolling() {
        if (state.pollTimer) {
            clearInterval(state.pollTimer);
            state.pollTimer = null;
        }
    }

    // ==================== Overview updates ====================
    function updateSummaryView(summary) {
        if (!$module || !summary) {
            return;
        }
        const total = formatBytes(summary.total_bytes);
        const used = formatBytes(summary.used_bytes);
        const free = formatBytes(summary.free_bytes);
        const usagePercent = `${summary.usage_percent?.toFixed?.(2) ?? summary.usage_percent}%`;

        $module.find('#storage-total').text(total);
        $module.find('#storage-used').text(used);
        $module.find('#storage-free').text(free);
        $module.find('#storage-usage-percent').text(usagePercent);
        $module.find('#storage-summary-timestamp').text(formatRelativeTime(summary.timestamp));

        const readMb = bytesToMB(summary.io?.read_bytes_per_sec);
        const writeMb = bytesToMB(summary.io?.write_bytes_per_sec);
        $module.find('#storage-read-rate').text(`${readMb.toFixed(2)} MB/s`);
        $module.find('#storage-write-rate').text(`${writeMb.toFixed(2)} MB/s`);

        updateUsageChart(summary);
        updateHistoryChart(summary.history || []);
        updateIoChart(summary.history || []);
        updateTrendCharts(summary.history || []);
    }

    function updateUsageChart(summary) {
        const ring = state.charts.usageRing;
        if (!ring) {
            return;
        }
        const used = summary.used_bytes ?? 0;
        const free = Math.max((summary.total_bytes ?? 0) - used, 0);
        ring.data.datasets[0].data = [used, free];
        ring.update('none');
    }

    function updateHistoryChart(history) {
        const historyChart = state.charts.historyLine;
        if (!historyChart) {
            return;
        }
        const labels = history.map(item => formatShortTime(item.timestamp));
        const usage = history.map(item => parseFloat(item.usage_percent ?? 0));
        historyChart.data.labels = labels;
        historyChart.data.datasets[0].data = usage;
        historyChart.update('none');

        const overview = state.charts.overviewLine;
        if (overview) {
            overview.data.labels = labels;
            overview.data.datasets[0].data = usage;
            overview.update('none');
        }
    }

    function updateIoChart(history) {
        const ioChart = state.charts.ioLine;
        if (!ioChart) {
            return;
        }
        const labels = history.map(item => formatShortTime(item.timestamp));
        const readData = history.map(item => bytesToMB(item.read_bytes_per_sec));
        const writeData = history.map(item => bytesToMB(item.write_bytes_per_sec));
        ioChart.data.labels = labels;
        ioChart.data.datasets[0].data = readData;
        ioChart.data.datasets[1].data = writeData;
        ioChart.update('none');
    }

    function updateTrendCharts(history) {
        const usageChart = state.charts.trendUsage;
        const ioChart = state.charts.trendIo;
        if (!usageChart || !ioChart) {
            return;
        }
        const labels = history.map(item => formatLongTime(item.timestamp));
        const usage = history.map(item => parseFloat(item.usage_percent ?? 0));
        const readData = history.map(item => bytesToMB(item.read_bytes_per_sec));
        const writeData = history.map(item => bytesToMB(item.write_bytes_per_sec));

        usageChart.data.labels = labels;
        usageChart.data.datasets[0].data = usage;
        usageChart.update('none');

        ioChart.data.labels = labels;
        ioChart.data.datasets[0].data = readData;
        ioChart.data.datasets[1].data = writeData;
        ioChart.update('none');
    }

    function updateOverviewMetrics(summary, partitions) {
        if (!summary) {
            return;
        }
        $module.find('#storage-overview-total').text(formatBytes(summary.total_bytes));

        const history = summary.history || [];
        if (history.length) {
            const average = history.reduce((acc, item) => acc + (item.usage_percent || 0), 0) / history.length;
            const peakRead = Math.max(...history.map(item => bytesToMB(item.read_bytes_per_sec || 0)), 0);
            const peakWrite = Math.max(...history.map(item => bytesToMB(item.write_bytes_per_sec || 0)), 0);
            $module.find('#storage-overview-average').text(`${average.toFixed(2)} %`);
            $module.find('#storage-overview-peak').text(`读 ${peakRead.toFixed(2)} / 写 ${peakWrite.toFixed(2)} MB/s`);
        } else {
            $module.find('#storage-overview-average').text('--');
            $module.find('#storage-overview-peak').text('--');
        }

        const hotspotCount = partitions.filter(part => part.warning).length;
        $module.find('#storage-overview-hotspots').text(hotspotCount);
    }

    function updatePartitionsView(partitions) {
        const $tbody = $module.find('#storage-partition-tbody');
        $tbody.empty();
        if (!partitions.length) {
            $tbody.append('<tr class="empty-row"><td colspan="8">未获取到分区信息</td></tr>');
            return;
        }

        partitions.forEach(part => {
            const readRate = `${bytesToMB(part.read_bytes_per_sec).toFixed(2)} MB/s`;
            const writeRate = `${bytesToMB(part.write_bytes_per_sec).toFixed(2)} MB/s`;
            const usage = `${(part.used_percent ?? 0).toFixed(2)} %`;
            const used = formatBytes(part.used_bytes);
            const total = formatBytes(part.total_bytes);
            const rowClass = part.warning ? 'warning' : '';
            const badge = part.warning ? '<span class="badge-warning">高占用</span>' : '';

            const row = `
                <tr class="${rowClass}">
                    <td>${escapeHtml(part.device)} ${badge}</td>
                    <td>${escapeHtml(part.mountpoint)}</td>
                    <td>${escapeHtml(part.fstype)}</td>
                    <td>${total}</td>
                    <td>${used}</td>
                    <td>${usage}</td>
                    <td>${readRate}</td>
                    <td>${writeRate}</td>
                </tr>
            `;
            $tbody.append(row);
        });
    }

    // ==================== Administrative actions ====================
    function handleAdminModeChange(event) {
        state.isAdmin = !!event?.detail?.active;
        updateAdminUI();
        if (state.isAdmin) {
            refreshOperationLog();
        }
    }

    function updateAdminUI() {
        if (!$module) {
            return;
        }
        const $status = $module.find('#storage-admin-status');
        const $placeholder = $module.find('#storage-admin-placeholder');
        const $sections = $module.find('.operations-section');
        const $grid = $module.find('#storage-operations-grid');
        const $footer = $module.find('.operations-footer');

        if (state.isAdmin) {
            $status.text('管理员模式已激活');
            $status.removeClass('bg-warning-subtle text-warning').addClass('bg-success-subtle text-success');
            $placeholder.addClass('d-none');
            $grid.removeClass('d-none');
            $footer.removeClass('d-none');
            $sections.removeClass('disabled');
            $sections.find('input, button, select, textarea').prop('disabled', false);
        } else {
            $status.text('需要管理员模式');
            $status.removeClass('bg-success-subtle text-success').addClass('bg-warning-subtle text-warning');
            $placeholder.removeClass('d-none');
            $grid.addClass('d-none');
            $footer.addClass('d-none');
            $sections.addClass('disabled');
            $sections.find('input, button, select, textarea').prop('disabled', true);
            clearCountdowns();
        }
    }

    async function onCleanupDryRun() {
        if (!state.isAdmin) {
            notify('warning', '请先激活管理员模式');
            return;
        }
        try {
            const payload = buildCleanupPayload(true);
            const result = await StorageApi.cleanup(payload);
            const freed = formatBytes(result.freed_bytes || 0);
            $module.find('#storage-cleanup-result').text(`预估可释放空间 ${freed}`);
            notify('success', '预估完成，确认后可执行清理');
            refreshOperationLog();
        } catch (error) {
            handleOperationError(error, '#storage-cleanup-result');
        }
    }

    async function onCleanupExecute() {
        if (!state.isAdmin) {
            notify('warning', '请先激活管理员模式');
            return;
        }
        try {
            const payload = buildCleanupPayload(false);
            const result = await StorageApi.cleanup(payload);
            const freed = formatBytes(result.freed_bytes || 0);
            $module.find('#storage-cleanup-result').text(`清理完成，约释放 ${freed}`);
            notify('success', '清理任务已完成');
            refreshOperationLog();
            loadStorageData();
        } catch (error) {
            handleOperationError(error, '#storage-cleanup-result');
        } finally {
            resetCountdownButton($module.find('[data-action="cleanup-run"]'));
        }
    }

    function buildCleanupPayload(dryRun) {
        const form = $module.find('#storage-cleanup-form');
        const target = form.find('input[name="cleanup-target"]:checked').val();
        const customPath = form.find('input[name="custom-path"]').val()?.trim() || null;
        return {
            target,
            custom_path: target === 'custom' ? customPath : null,
            dry_run: !!dryRun
        };
    }

    async function onMountSubmit() {
        if (!state.isAdmin) {
            notify('warning', '请先激活管理员模式');
            return;
        }
        const form = $module.find('#storage-mount-form');
        const device = form.find('input[name="device"]').val().trim();
        const mountpoint = form.find('input[name="mountpoint"]').val().trim();
        const options = form.find('input[name="options"]').val().trim();
        const action = form.find('input[name="action"]:checked').val();

        const payload = {
            device,
            mountpoint: mountpoint || null,
            options: options || null,
            action
        };

        try {
            await StorageApi.mount(payload);
            $module.find('#storage-mount-result').text(`${action === 'mount' ? '挂载' : '卸载'}操作已完成`);
            notify('success', '挂载操作执行成功');
            refreshOperationLog();
            loadStorageData();
        } catch (error) {
            handleOperationError(error, '#storage-mount-result');
        } finally {
            resetCountdownButton($module.find('#storage-mount-form .countdown-btn'));
        }
    }

    async function onPartitionSubmit() {
        if (!state.isAdmin) {
            notify('warning', '请先激活管理员模式');
            return;
        }
        const form = $module.find('#storage-partition-form');
        const payload = {
            device: form.find('input[name="device"]').val().trim(),
            operation: form.find('select[name="operation"]').val(),
            filesystem: form.find('select[name="filesystem"]').val(),
            label: form.find('input[name="label"]').val().trim() || null,
            extra_args: form.find('input[name="extra"]').val().trim() || null
        };

        if (payload.operation === 'wipefs') {
            payload.filesystem = null;
            payload.label = null;
        }

        try {
            await StorageApi.partition(payload);
            $module.find('#storage-partition-result').text('操作已执行，请重新加载分区信息确认结果');
            notify('success', '分区操作提交成功');
            refreshOperationLog();
            setTimeout(loadStorageData, 2000);
        } catch (error) {
            handleOperationError(error, '#storage-partition-result');
        } finally {
            resetCountdownButton($module.find('#storage-partition-form .countdown-btn'));
        }
    }

    async function onSmartSubmit() {
        if (!state.isAdmin) {
            notify('warning', '请先激活管理员模式');
            return;
        }
        const form = $module.find('#storage-smart-form');
        const payload = {
            device: form.find('input[name="device"]').val().trim(),
            mode: form.find('select[name="mode"]').val()
        };
        try {
            const result = await StorageApi.smartSelftest(payload);
            const msg = result.estimated_minutes
                ? `自检已启动，预计 ${result.estimated_minutes} 分钟完成`
                : '自检已启动';
            updateSmartReportMessage(msg);
            notify('success', msg);
            refreshOperationLog();
        } catch (error) {
            handleOperationError(error, null, updateSmartReportMessage);
        } finally {
            resetCountdownButton($module.find('#storage-smart-form .countdown-btn'));
        }
    }

    async function onSmartReport() {
        if (!state.isAdmin) {
            notify('warning', '请先激活管理员模式');
            return;
        }
        const device = $module.find('#storage-smart-form input[name="device"]').val().trim();
        if (!device) {
            notify('warning', '请先输入设备路径');
            return;
        }
        try {
            const report = await StorageApi.smartReport(device);
            renderSmartReport(report);
            notify('success', '已获取 SMART 报告');
            refreshOperationLog();
        } catch (error) {
            handleOperationError(error, null, updateSmartReportMessage);
        }
    }

    async function refreshOperationLog() {
        if (!state.isAdmin) {
            return;
        }
        try {
            const data = await StorageApi.operations();
            renderOperationLog(data?.operations || []);
        } catch (error) {
            console.warn('获取操作日志失败:', error);
        }
    }

    function renderOperationLog(logs) {
        const container = $module.find('#storage-operations-log');
        container.empty();
        if (!logs.length) {
            container.append('<div class="log-empty">暂无记录</div>');
            return;
        }
        logs.forEach(entry => {
            const statusClass = entry.status === 'success' ? 'success'
                : entry.status === 'dry-run' || entry.status === 'started'
                    ? 'info'
                    : 'failed';
            const statusLabel = mapStatus(entry.status);
            const actionLabel = mapAction(entry.action);
            const timestamp = formatLongTime(entry.timestamp);
            const message = escapeHtml(entry.message || '');
            const logId = entry.id;
            const detail = escapeHtml(JSON.stringify(entry.detail || {}));

            const item = `
                <div class="log-item ${statusClass}">
                    <div class="log-header">
                        <span>${actionLabel}</span>
                        <span>${statusLabel}</span>
                    </div>
                    <div class="log-detail">${message}</div>
                    <div class="log-meta">
                        <span>${timestamp}</span>
                        <span>#${logId.slice(0, 8)}</span>
                    </div>
                    <div class="log-meta">
                        <span>操作人: ${escapeHtml(entry.actor || '-')}</span>
                        <span>参数: ${detail}</span>
                    </div>
                </div>
            `;
            container.append(item);
        });
    }

    function mapAction(action) {
        switch (action) {
            case 'cleanup': return '目录清理';
            case 'mount': return '挂载管理';
            case 'partition': return '分区/格式化';
            case 'smart-selftest': return 'SMART 自检';
            case 'smart-report': return 'SMART 报告';
            default: return action || '未知操作';
        }
    }

    function mapStatus(status) {
        switch (status) {
            case 'success': return '成功';
            case 'failed': return '失败';
            case 'dry-run': return '预估';
            case 'started': return '已启动';
            case 'completed': return '已完成';
            default: return status || '未知';
        }
    }

    function updateSmartReportMessage(message) {
        $module.find('#storage-smart-report').html(escapeHtml(message || ''));
    }

    function renderSmartReport(report) {
        if (!report) {
            updateSmartReportMessage('无法解析 SMART 报告');
            return;
        }
        const attrs = (report.attributes || []).map(attr => `
            <tr>
                <td>${escapeHtml(String(attr.id ?? ''))}</td>
                <td>${escapeHtml(attr.name || '')}</td>
                <td>${escapeHtml(String(attr.value ?? ''))}</td>
                <td>${escapeHtml(String(attr.worst ?? ''))}</td>
                <td>${escapeHtml(String(attr.threshold ?? ''))}</td>
                <td>${escapeHtml(String(attr.raw ?? ''))}</td>
            </tr>
        `).join('');

        const tests = (report.selftest || []).map(test => `
            <li>${escapeHtml(test.type || '')} - ${escapeHtml(test.status || '')} (寿命 ${escapeHtml(String(test.lifetime_hours ?? '-'))}h)</li>
        `).join('');

        const health = escapeHtml(report.overall_health || '未知');
        const html = `
            <div><strong>设备</strong>：${escapeHtml(report.device || '')}</div>
            <div><strong>型号</strong>：${escapeHtml(report.model || '-')}</div>
            <div><strong>序列号</strong>：${_escapeOrDash(report.serial)}</div>
            <div><strong>固件</strong>：${_escapeOrDash(report.firmware)}</div>
            <div><strong>温度</strong>：${report.temperature != null ? `${report.temperature} ℃` : '--'}</div>
            <div><strong>健康状态</strong>：${health}</div>
            <div><strong>生成时间</strong>：${formatLongTime(report.generated_at)}</div>
            <hr>
            <div><strong>SMART 属性</strong></div>
            <table class="table table-sm table-dark table-striped mt-2">
                <thead>
                    <tr>
                        <th>ID</th><th>名称</th><th>当前</th><th>历史最低</th><th>阈值</th><th>原始值</th>
                    </tr>
                </thead>
                <tbody>${attrs || '<tr><td colspan="6">无属性数据</td></tr>'}</tbody>
            </table>
            <div><strong>自检记录</strong></div>
            <ul>${tests || '<li>暂无记录</li>'}</ul>
        `;
        $module.find('#storage-smart-report').html(html);
    }

    function _escapeOrDash(value) {
        return value ? escapeHtml(value) : '--';
    }

    function handleOperationError(error, selector = null, fallback) {
        console.error('操作失败:', error);
        const message = error?.data?.message || error?.message || '操作失败';
        const logId = error?.data?.log_id || error?.log_id;
        const text = logId ? `${message} (log_id=${logId})` : message;
        if (selector) {
            $module.find(selector).text(text);
        } else if (fallback) {
            fallback(text);
        }
        notify('error', text);
        refreshOperationLog();
    }

    // ==================== Report export ====================
    async function onExportReport(event) {
        const format = $(event.currentTarget).data('format') || 'csv';
        try {
            const response = await StorageApi.downloadReport(format);
            const blob = await response.blob();
            const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
            const fileName = `storage-report-${timestamp}.${format}`;
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = fileName;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);

            state.lastReportInfo = `${format.toUpperCase()} 报表已导出 (${formatTimeReadable(new Date())})`;
            $module.find('#storage-report-status').text(state.lastReportInfo);
            notify('success', '报表导出成功');
        } catch (error) {
            console.error('导出失败:', error);
            const message = error.detail || error.message || '导出失败';
            $module.find('#storage-report-status').text(message);
            notify('error', message);
        }
    }

    // ==================== Countdown buttons ====================
    function attachCountdownButton($button, callback) {
        if (!$button.length) {
            return;
        }
        $button.on('click', async (event) => {
            event.preventDefault();
            if (!state.isAdmin) {
                notify('warning', '请先激活管理员模式');
                return;
            }

            const buttonEl = event.currentTarget;
            if (buttonEl.dataset.ready === 'true') {
                buttonEl.dataset.ready = 'false';
                try {
                    await callback(event);
                } finally {
                    resetCountdownButton($(buttonEl));
                }
                return;
            }

            startCountdown($(buttonEl));
        });
    }

    function startCountdown($button) {
        const seconds = Number($button.data('countdown')) || 8;
        const original = $button.data('original');
        if (!original) {
            $button.data('original', $button.html());
        }
        if (state.countdowns.has($button[0])) {
            return;
        }
        let remaining = seconds;
        $button.addClass('disabled');
        $button.html(`确认中 (${remaining})`);

        const interval = setInterval(() => {
            remaining -= 1;
            if (remaining <= 0) {
                clearInterval(interval);
                $button.removeClass('disabled');
                $button.addClass('btn-warning');
                $button.html('点击确认');
                $button[0].dataset.ready = 'true';
                const timeout = setTimeout(() => resetCountdownButton($button), 10000);
                state.countdowns.set($button[0], { interval: null, timeout });
            } else {
                $button.html(`确认中 (${remaining})`);
            }
        }, 1000);

        state.countdowns.set($button[0], { interval, timeout: null });
    }

    function resetCountdownButton($button) {
        const buttonEl = $button[0];
        const record = state.countdowns.get(buttonEl);
        if (record?.interval) {
            clearInterval(record.interval);
        }
        if (record?.timeout) {
            clearTimeout(record.timeout);
        }
        state.countdowns.delete(buttonEl);
        const original = $button.data('original');
        if (original) {
            $button.html(original);
        }
        $button.removeClass('disabled btn-warning');
        delete buttonEl.dataset.ready;
    }

    function clearCountdowns() {
        for (const [buttonEl, record] of state.countdowns.entries()) {
            if (record.interval) {
                clearInterval(record.interval);
            }
            if (record.timeout) {
                clearTimeout(record.timeout);
            }
            const $button = $(buttonEl);
            const original = $button.data('original');
            if (original) {
                $button.html(original);
            }
            $button.removeClass('disabled btn-warning');
            delete buttonEl.dataset.ready;
        }
        state.countdowns.clear();
    }

    // ==================== Chart initialization ====================
    function setupCharts() {
        const usageCtx = document.getElementById('storage-usage-chart')?.getContext('2d');
        if (usageCtx) {
            state.charts.usageRing = new Chart(usageCtx, {
                type: 'doughnut',
                data: {
                    labels: ['已用', '剩余'],
                    datasets: [{
                        data: [0, 1],
                        backgroundColor: ['#6366f1', '#dbeafe'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '70%',
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        }

        const ioCtx = document.getElementById('storage-io-chart')?.getContext('2d');
        if (ioCtx) {
            state.charts.ioLine = new Chart(ioCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: '读 MB/s',
                            data: [],
                            borderColor: '#60a5fa',
                            backgroundColor: 'rgba(96, 165, 250, 0.25)',
                            tension: 0.3,
                            fill: true,
                            pointRadius: 0
                        },
                        {
                            label: '写 MB/s',
                            data: [],
                            borderColor: '#f87171',
                            backgroundColor: 'rgba(248, 113, 113, 0.25)',
                            tension: 0.3,
                            fill: true,
                            pointRadius: 0
                        }
                    ]
                },
                options: lineChartOptions('MB/s')
            });
        }

        const historyCtx = document.getElementById('storage-history-chart')?.getContext('2d');
        if (historyCtx) {
            state.charts.historyLine = new Chart(historyCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: '使用率 %',
                        data: [],
                        borderColor: '#a855f7',
                        backgroundColor: 'rgba(168, 85, 247, 0.25)',
                        tension: 0.3,
                        fill: true,
                        pointRadius: 0
                    }]
                },
                options: lineChartOptions('%')
            });
        }

        const overviewCtx = document.getElementById('storage-overview-chart')?.getContext('2d');
        if (overviewCtx) {
            state.charts.overviewLine = new Chart(overviewCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: '使用率 %',
                        data: [],
                        borderColor: '#312e81',
                        backgroundColor: 'rgba(49, 46, 129, 0.3)',
                        tension: 0.35,
                        fill: true,
                        pointRadius: 0
                    }]
                },
                options: {
                    ...lineChartOptions('%'),
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: { display: false },
                        y: {
                            ticks: { color: '#cbd5f5' },
                            grid: { color: 'rgba(148, 163, 226, 0.2)' }
                        }
                    }
                }
            });
        }

        const trendUsageCtx = document.getElementById('storage-trend-usage')?.getContext('2d');
        if (trendUsageCtx) {
            state.charts.trendUsage = new Chart(trendUsageCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: '使用率 %',
                        data: [],
                        borderColor: '#22d3ee',
                        backgroundColor: 'rgba(34, 211, 238, 0.3)',
                        tension: 0.3,
                        fill: true,
                        pointRadius: 0
                    }]
                },
                options: darkLineChartOptions('%')
            });
        }

        const trendIoCtx = document.getElementById('storage-trend-io')?.getContext('2d');
        if (trendIoCtx) {
            state.charts.trendIo = new Chart(trendIoCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: '读 MB/s',
                            data: [],
                            borderColor: '#34d399',
                            backgroundColor: 'rgba(52, 211, 153, 0.3)',
                            tension: 0.3,
                            fill: true,
                            pointRadius: 0
                        },
                        {
                            label: '写 MB/s',
                            data: [],
                            borderColor: '#f87171',
                            backgroundColor: 'rgba(248, 113, 113, 0.3)',
                            tension: 0.3,
                            fill: true,
                            pointRadius: 0
                        }
                    ]
                },
                options: darkLineChartOptions('MB/s')
            });
        }
    }

    function lineChartOptions(unit) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            scales: {
                x: {
                    display: false
                },
                y: {
                    ticks: {
                        callback: value => `${value} ${unit}`,
                        color: '#1f2937'
                    },
                    grid: {
                        color: 'rgba(59, 130, 246, 0.1)'
                    }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: context => `${context.dataset.label}: ${context.parsed.y.toFixed(2)} ${unit}`
                    }
                }
            }
        };
    }

    function darkLineChartOptions(unit) {
        const base = lineChartOptions(unit);
        return {
            ...base,
            scales: {
                x: {
                    ticks: { color: '#94a3b8' },
                    grid: { color: 'rgba(148, 163, 226, 0.2)' }
                },
                y: {
                    ticks: {
                        color: '#94a3b8',
                        callback: value => `${value} ${unit}`
                    },
                    grid: {
                        color: 'rgba(148, 163, 226, 0.2)'
                    }
                }
            }
        };
    }

    function destroyCharts() {
        Object.keys(state.charts).forEach(key => {
            if (state.charts[key]) {
                state.charts[key].destroy();
                state.charts[key] = null;
            }
        });
    }

    function scheduleTrendChartResize() {
        // Wait for the tab transition animation to finish before recalculating dimensions to avoid hidden-state sizing issues
        window.requestAnimationFrame(() => {
            setTimeout(() => {
                if (state.charts.trendUsage) {
                    state.charts.trendUsage.resize();
                }
                if (state.charts.trendIo) {
                    state.charts.trendIo.resize();
                }
            }, 0);
        });
    }

    // ==================== Helper functions ====================
    function formatBytes(bytes) {
        if (!Number.isFinite(bytes)) {
            return '--';
        }
        if (bytes === 0) {
            return '0 B';
        }
        const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
        const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
        const value = bytes / Math.pow(1024, exponent);
        return `${value.toFixed(value >= 100 ? 0 : value >= 10 ? 1 : 2)} ${units[exponent]}`;
    }

    function bytesToMB(value) {
        if (!Number.isFinite(value)) {
            return 0;
        }
        return value / (1024 * 1024);
    }

    function formatShortTime(timestamp) {
        const date = new Date(timestamp);
        if (Number.isNaN(date.getTime())) {
            return '--';
        }
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function formatLongTime(timestamp) {
        const date = new Date(timestamp);
        if (Number.isNaN(date.getTime())) {
            return timestamp || '--';
        }
        return date.toLocaleString();
    }

    function formatRelativeTime(timestamp) {
        const date = new Date(timestamp);
        if (Number.isNaN(date.getTime())) {
            return '--';
        }
        const diff = (Date.now() - date.getTime()) / 1000;
        if (diff < 60) return '刚刚';
        if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
        return date.toLocaleTimeString();
    }

    function formatTimeReadable(date) {
        return date.toLocaleString();
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function notify(type, message) {
        if (!message) {
            return;
        }
        if (window.toastr) {
            if (type === 'success') {
                toastr.success(message, '成功');
            } else if (type === 'warning') {
                toastr.warning(message, '提示');
            } else if (type === 'error') {
                toastr.error(message, '错误');
            } else {
                toastr.info(message, '信息');
            }
        } else {
            console.log(`[${type}]`, message);
        }
    }
})();
