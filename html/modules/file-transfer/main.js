/**
 * File transfer module logic for browsing directories, uploads, and deletions.
 */

(() => {
    const FILE_API_BASE = '/api/files';
    const FILE_MAX_UPLOAD_BYTES = 50 * 1024 * 1024; // Mirror backend limit: 50 MB

    const fileState = {
        currentPath: '',
        absolutePath: '',
        rootPath: '',
        isAdmin: false,
        entries: [],
        loading: false
    };

    let $module = null;
    let handleAdminModeChangeBound = null;

    // Module lifecycle hooks.
    function moduleInit() {
        console.log('文件传输模块初始化');
        $module = $('.file-transfer-module');
        bindEvents();
        handleAdminModeChangeBound = onAdminModeChange.bind(null);
        window.addEventListener('rosdeck:admin-mode-change', handleAdminModeChangeBound);
        loadDirectory('');
    }

    function moduleCleanup() {
        if ($module) {
            $module.find('#file-upload-btn').off('click', onUploadButtonClick);
            $module.find('#file-upload-input').off('change', onFileInputChange);
            $module.find('#file-refresh-btn').off('click', onRefreshClick);
            $module.find('#file-go-up-btn').off('click', onGoUpClick);
            $module.find('#file-breadcrumbs').off('click', 'button', onBreadcrumbClick);
            $module.find('#file-table-body')
                .off('click', '.entry-open', onEntryOpenClick)
                .off('click', '.entry-download', onEntryDownloadClick)
                .off('click', '.entry-delete', onEntryDeleteClick)
                .off('dblclick', 'tr.file-entry', onEntryDoubleClick);
        }
        $module = null;
        if (handleAdminModeChangeBound) {
            window.removeEventListener('rosdeck:admin-mode-change', handleAdminModeChangeBound);
            handleAdminModeChangeBound = null;
        }
        resetState();
        console.log('文件传输模块卸载完成');
    }

    // Event bindings.
    function bindEvents() {
        $module.find('#file-upload-btn').on('click', onUploadButtonClick);
        $module.find('#file-upload-input').on('change', onFileInputChange);
        $module.find('#file-refresh-btn').on('click', onRefreshClick);
        $module.find('#file-go-up-btn').on('click', onGoUpClick);
        $module.find('#file-breadcrumbs').on('click', 'button', onBreadcrumbClick);
        $module.find('#file-table-body')
            .on('click', '.entry-open', onEntryOpenClick)
            .on('click', '.entry-download', onEntryDownloadClick)
            .on('click', '.entry-delete', onEntryDeleteClick)
            .on('dblclick', 'tr.file-entry', onEntryDoubleClick);
    }

    function resetState() {
        fileState.currentPath = '';
        fileState.absolutePath = '';
        fileState.rootPath = '';
        fileState.isAdmin = false;
        fileState.entries = [];
        fileState.loading = false;
    }

    // Event handlers.
    function onUploadButtonClick() {
        $module.find('#file-upload-input').trigger('click');
    }

    function onFileInputChange(event) {
        const files = Array.from(event.target.files || []);
        event.target.value = '';
        if (!files.length) {
            return;
        }
        handleUploads(files);
    }

    function onRefreshClick() {
        loadDirectory(fileState.currentPath);
    }

    function onGoUpClick() {
        const parent = getParentPath(fileState.currentPath, fileState.isAdmin);
        loadDirectory(parent);
    }

    function onBreadcrumbClick(event) {
        const path = $(event.currentTarget).data('path');
        if (typeof path === 'undefined') {
            return;
        }
        loadDirectory(path);
    }

    function onEntryOpenClick(event) {
        const $row = $(event.currentTarget).closest('tr.file-entry');
        const path = $row.data('path');
        if (!$row.data('is-dir') || typeof path === 'undefined') {
            return;
        }
        loadDirectory(path);
    }

    function onEntryDownloadClick(event) {
        const $row = $(event.currentTarget).closest('tr.file-entry');
        const path = $row.data('path');
        if (!$row.data('is-dir') && path !== undefined) {
            triggerDownload(path);
        }
    }

    function onEntryDeleteClick(event) {
        const $row = $(event.currentTarget).closest('tr.file-entry');
        const path = $row.data('path');
        const name = $row.data('name');
        if (typeof path === 'undefined') {
            return;
        }
        handleDelete(path, name);
    }

    function onEntryDoubleClick(event) {
        const $row = $(event.currentTarget);
        const path = $row.data('path');
        if (!$row.data('is-dir') && path !== undefined) {
            triggerDownload(path);
        } else if ($row.data('is-dir') && path !== undefined) {
            loadDirectory(path);
        }
    }

    function onAdminModeChange(event) {
        if (!event || typeof event.detail !== 'object') {
            return;
        }
        const isActive = !!event.detail.active;
        if (isActive) {
            loadDirectory(fileState.currentPath || '');
        } else {
            loadDirectory('');
        }
    }

    // Core operations.
    async function loadDirectory(path) {
        setLoading(true);
        try {
            const params = new URLSearchParams();
            if (path) {
                params.set('path', path);
            }
            const url = params.toString() ? `${FILE_API_BASE}/list?${params.toString()}` : `${FILE_API_BASE}/list`;
            const response = await fetch(url, { credentials: 'include' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const data = await response.json();
            updateStateFromResponse(data);
            renderView(data);
        } catch (error) {
            console.error('加载目录失败:', error);
            if (typeof toastr !== 'undefined') {
                toastr.error('无法加载目录，请稍后重试', '错误');
            }
            renderErrorState();
        } finally {
            setLoading(false);
        }
    }

    function updateStateFromResponse(data) {
        fileState.currentPath = data.current_path || '';
        fileState.absolutePath = data.absolute_path || '';
        fileState.rootPath = data.root_path || '';
        fileState.isAdmin = !!data.is_admin;
        fileState.entries = Array.isArray(data.entries) ? data.entries : [];
    }

    async function handleUploads(files) {
        for (const file of files) {
            const success = await uploadSingleFile(file);
            if (!success) {
                break;
            }
        }
        await loadDirectory(fileState.currentPath);
    }

    async function uploadSingleFile(file) {
        if (file.size > FILE_MAX_UPLOAD_BYTES) {
            if (typeof toastr !== 'undefined') {
                toastr.error(`文件 ${file.name} 超过 ${Math.round(FILE_MAX_UPLOAD_BYTES / (1024 * 1024))} MB 限制`, '上传失败');
            }
            return false;
        }

        const csrfToken = await ensureCsrfToken();
        if (!csrfToken) {
            if (typeof toastr !== 'undefined') {
                toastr.error('无法获取 CSRF Token，请重新登录', '上传失败');
            }
            return false;
        }

        const formData = new FormData();
        formData.append('file', file);

        const params = new URLSearchParams();
        if (fileState.currentPath) {
            params.set('directory', fileState.currentPath);
        }

        const url = params.toString() ? `${FILE_API_BASE}/upload?${params.toString()}` : `${FILE_API_BASE}/upload`;

        try {
            if (typeof toastr !== 'undefined') {
                toastr.info(`正在上传 ${file.name}...`, '上传中');
            }

            const response = await fetch(url, {
                method: 'POST',
                body: formData,
                headers: { 'X-CSRF-Token': csrfToken },
                credentials: 'include'
            });

            const payload = await response.json().catch(() => ({}));

            if (!response.ok) {
                const message = payload?.detail?.message || payload?.message || `上传失败 (HTTP ${response.status})`;
                if (typeof toastr !== 'undefined') {
                    toastr.error(message, '上传失败');
                }
                return false;
            }

            if (typeof toastr !== 'undefined') {
                toastr.success(`文件 ${file.name} 上传成功`, '完成');
            }
            return true;
        } catch (error) {
            console.error('上传文件出现异常:', error);
            if (typeof toastr !== 'undefined') {
                toastr.error('上传过程中出现错误，请检查网络', '上传失败');
            }
            return false;
        }
    }

    async function handleDelete(path, name = '') {
        const target = name || path.split('/').pop();
        if (!confirm(`确认删除文件 "${target}" 吗？此操作不可恢复。`)) {
            return;
        }

        const csrfToken = await ensureCsrfToken();
        if (!csrfToken) {
            if (typeof toastr !== 'undefined') {
                toastr.error('无法获取 CSRF Token，请重新登录', '删除失败');
            }
            return;
        }

        const params = new URLSearchParams();
        params.set('path', path);

        try {
            const response = await fetch(`${FILE_API_BASE}?${params.toString()}`, {
                method: 'DELETE',
                headers: { 'X-CSRF-Token': csrfToken },
                credentials: 'include'
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                const message = payload?.detail?.message || payload?.message || `删除失败 (HTTP ${response.status})`;
                if (typeof toastr !== 'undefined') {
                    toastr.error(message, '删除失败');
                }
                return;
            }
            if (typeof toastr !== 'undefined') {
                toastr.success(`文件 ${target} 已删除`, '完成');
            }
            await loadDirectory(fileState.currentPath);
        } catch (error) {
            console.error('删除文件失败:', error);
            if (typeof toastr !== 'undefined') {
                toastr.error('删除过程中发生异常，请检查网络', '删除失败');
            }
        }
    }

    function triggerDownload(path) {
        const params = new URLSearchParams();
        params.set('path', path);
        const url = `${FILE_API_BASE}/download?${params.toString()}`;
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.target = '_blank';
        anchor.rel = 'noopener';
        document.body.appendChild(anchor);
        anchor.click();
        setTimeout(() => {
            document.body.removeChild(anchor);
        }, 0);
    }

    // Rendering helpers.
    function renderView(data) {
        renderSummary(data);
        renderBreadcrumbs(data);
        renderEntries(data);
        updateWarningBanner(data);
        updateToolbarState();
    }

    function renderSummary(data) {
        const modeText = data.is_admin ? '管理员模式' : '普通模式';
        $module.find('#file-mode-indicator').text(modeText);
        $module.find('#file-root-indicator').text(data.root_path || '--');
        $module.find('#file-location').text(data.absolute_path || data.root_path || '--');
    }

    function renderBreadcrumbs(data) {
        const $breadcrumbs = $module.find('#file-breadcrumbs');
        $breadcrumbs.empty();
        const list = Array.isArray(data.breadcrumbs) ? data.breadcrumbs : [];
        if (!list.length) {
            $breadcrumbs.append('<span class="breadcrumb-item active">/</span>');
            return;
        }

        list.forEach((crumb, idx) => {
            const isLast = idx === list.length - 1;
            const path = typeof crumb.path === 'string' ? crumb.path : '';
            if (isLast) {
                $breadcrumbs.append(`<span class="breadcrumb-item active">${escapeHtml(crumb.name || '/')}</span>`);
            } else {
                const button = $('<button class="breadcrumb-item link"></button>')
                    .attr('type', 'button')
                    .data('path', path)
                    .text(crumb.name || '/');
                $breadcrumbs.append(button);
                $breadcrumbs.append('<span class="breadcrumb-separator">/</span>');
            }
        });
    }

    function renderEntries(data) {
        const $tbody = $module.find('#file-table-body');
        const $empty = $module.find('#file-empty');

        $tbody.empty();

        const entries = Array.isArray(data.entries) ? data.entries : [];

        if (!entries.length) {
            $empty.show();
            return;
        }
        $empty.hide();

        entries.forEach(entry => {
            const isDir = !!entry.is_dir;
            const isSymlink = !!entry.is_symlink;
            const row = $('<tr class="file-entry"></tr>')
                .data('path', entry.path)
                .data('name', entry.name)
                .data('is-dir', isDir)
                .data('absolute', entry.absolute_path || entry.path);

            const iconClass = isDir ? 'bi-folder2' : (isSymlink ? 'bi-link-45deg' : 'bi-file-earmark');
            const nameCell = $(`
                <td class="col-name">
                    <div class="entry-name">
                        <i class="bi ${iconClass}"></i>
                        <span class="entry-label">${escapeHtml(entry.name)}</span>
                    </div>
                </td>
            `);

            const sizeCell = $('<td class="col-size"></td>').text(isDir ? '-' : formatBytes(entry.size));
            const modifiedCell = $('<td class="col-modified"></td>').text(formatModified(entry.modified));

            const actionsCell = $('<td class="col-actions"></td>');
            if (isDir) {
                const openBtn = $('<button class="btn-icon entry-open" title="打开目录"><i class="bi bi-folder-symlink"></i></button>');
                actionsCell.append(openBtn);
            } else {
                const downloadBtn = $('<button class="btn-icon entry-download" title="下载"><i class="bi bi-download"></i></button>');
                const deleteBtn = $('<button class="btn-icon danger entry-delete" title="删除"><i class="bi bi-trash"></i></button>');
                actionsCell.append(downloadBtn, deleteBtn);
            }

            row.append(nameCell, sizeCell, modifiedCell, actionsCell);
            $tbody.append(row);
        });
    }

    function renderErrorState() {
        const $tbody = $module.find('#file-table-body');
        const $empty = $module.find('#file-empty');
        $tbody.empty();
        $empty.hide();
        $tbody.append(`
            <tr>
                <td colspan="4" class="error-cell">
                    <i class="bi bi-exclamation-octagon"></i>
                    <span>目录加载失败</span>
                </td>
            </tr>
        `);
    }

    function updateToolbarState() {
        const canGoUp = fileState.isAdmin
            ? !!fileState.currentPath && fileState.currentPath !== '/'
            : !!fileState.currentPath;
        $module.find('#file-go-up-btn').prop('disabled', !canGoUp);
    }

    function updateWarningBanner(data) {
        const $banner = $module.find('#file-warning-banner');
        const absolute = data.absolute_path || '';
        if (!data.is_admin) {
            $banner.hide();
            return;
        }

        if (shouldWarnForPath(absolute)) {
            $banner.show();
            $module.find('#file-warning-text').text('当前目录包含系统关键文件，操作前请确认风险。');
        } else {
            $banner.hide();
        }
    }

    // State utilities and UI helpers.
    function setLoading(isLoading) {
        fileState.loading = isLoading;
        const $tbody = $module.find('#file-table-body');
        const $empty = $module.find('#file-empty');
        if (isLoading) {
            $empty.hide();
            $tbody.empty().append(`
                <tr class="file-loading-row">
                    <td colspan="4">
                        <div class="loading-holder">
                            <i class="bi bi-arrow-clockwise spinner"></i>
                            正在加载...
                        </div>
                    </td>
                </tr>
            `);
        }
    }

    function getParentPath(path, isAdmin) {
        if (!path) {
            return '';
        }
        if (isAdmin) {
            if (path === '/' || path === '') {
                return '';
            }
            const parts = path.split('/').filter(Boolean);
            parts.pop();
            if (!parts.length) {
                return '/';
            }
            return `/${parts.join('/')}`;
        }

        const parts = path.split('/').filter(Boolean);
        parts.pop();
        return parts.join('/');
    }

    function formatBytes(size) {
        if (typeof size !== 'number' || Number.isNaN(size)) {
            return '-';
        }
        if (size < 1024) {
            return `${size} B`;
        }
        const units = ['KB', 'MB', 'GB', 'TB'];
        let value = size;
        let unitIndex = -1;
        do {
            value /= 1024;
            unitIndex += 1;
        } while (value >= 1024 && unitIndex < units.length - 1);
        return `${value.toFixed(1)} ${units[unitIndex]}`;
    }

    function formatModified(timestamp) {
        if (!timestamp || Number.isNaN(Number(timestamp))) {
            return '-';
        }
        const date = new Date(Number(timestamp) * 1000);
        if (Number.isNaN(date.getTime())) {
            return '-';
        }
        return date.toLocaleString();
    }

    function shouldWarnForPath(path) {
        if (!path || path === '/' || path === '') {
            return false;
        }
        const sensitivePrefixes = ['/etc', '/bin', '/sbin', '/usr', '/boot', '/var'];
        return sensitivePrefixes.some(prefix => path === prefix || path.startsWith(`${prefix}/`));
    }

    function escapeHtml(str) {
        if (typeof str !== 'string') {
            return '';
        }
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    window.moduleInit = moduleInit;
    window.moduleCleanup = moduleCleanup;
})();
