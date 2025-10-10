(() => {
    const selectors = {
        priority: '#logs-priority-select',
        keyword: '#logs-keyword-input',
        since: '#logs-since-input',
        until: '#logs-until-input',
        limit: '#logs-limit-input',
        searchBtn: '#logs-search-btn',
        resetBtn: '#logs-reset-btn',
        refreshBtn: '#logs-refresh-btn',
        loadMoreBtn: '#logs-load-more-btn',
        message: '#logs-message',
        list: '#logs-list',
        summary: '#logs-summary-text',
        accessBadge: '#logs-access-badge'
    };

    const state = {
        isAdmin: false,
        limits: { default: 100, max: 200 },
        cursor: null,
        hasMore: false,
        loading: false,
        entries: [],
        keyword: '',
        filtersChanged: false,
        metadataLoaded: false
    };

    let adminModeListener = null;
    const handlers = {};

    // -------- Utilities --------
    function qs(selector) {
        return document.querySelector(selector);
    }

    function htmlEscape(str) {
        return String(str || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatDateTime(value) {
        if (!value) {
            return '--';
        }
        const dt = new Date(value);
        if (Number.isNaN(dt.getTime())) {
            return value;
        }
        return dt.toLocaleString();
    }

    function escapeRegex(str) {
        return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }

    function highlight(text, keyword) {
        if (!keyword) {
            return htmlEscape(text);
        }
        try {
            const regex = new RegExp(escapeRegex(keyword), 'gi');
            return htmlEscape(text).replace(regex, match => `<mark>${match}</mark>`);
        } catch (err) {
            return htmlEscape(text);
        }
    }

    function isoFromInput(inputEl) {
        const raw = (inputEl.value || '').trim();
        if (!raw) {
            return null;
        }
        // datetime-local -> YYYY-MM-DDTHH:MM
        return `${raw}:00`; // journalctl 时间转换在后端完成
    }

    function setLoading(loading) {
        state.loading = loading;
        qs(selectors.searchBtn)?.classList.toggle('disabled', loading);
        qs(selectors.refreshBtn)?.classList.toggle('disabled', loading);
        qs(selectors.loadMoreBtn)?.classList.toggle('disabled', loading || !state.hasMore);

        if (loading) {
            setMessage('正在加载日志...');
        }
    }

    function setMessage(message, type = 'info') {
        const container = qs(selectors.message);
        if (!container) {
            return;
        }
        container.classList.remove('d-none', 'text-danger', 'text-warning', 'text-success');
        container.textContent = message;
        if (type === 'error') {
            container.classList.add('text-danger');
        } else if (type === 'warning') {
            container.classList.add('text-warning');
        } else if (type === 'success') {
            container.classList.add('text-success');
        }
        qs(selectors.list)?.classList.add('d-none');
    }

    function showList() {
        qs(selectors.message)?.classList.add('d-none');
        qs(selectors.list)?.classList.remove('d-none');
    }

    // -------- Metadata --------
    function fetchMetadata() {
        return fetch('/api/logs/metadata', { credentials: 'include' })
            .then(async response => {
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                state.isAdmin = !!data.is_admin;
                if (data.limits) {
                    state.limits = {
                        default: data.limits.default ?? state.limits.default,
                        max: data.limits.max ?? state.limits.max
                    };
                }
                populatePrioritySelect(data.priorities || []);
                const limitInput = qs(selectors.limit);
                if (limitInput) {
                    limitInput.value = state.limits.default;
                    limitInput.max = state.limits.max;
                }
                updateAccessBadge(data.access || {});
                state.metadataLoaded = true;
            })
            .catch(err => {
                console.error('metadata 加载失败:', err);
                setMessage('无法加载日志配置，请稍后重试', 'error');
            });
    }

    function populatePrioritySelect(priorities) {
        const select = qs(selectors.priority);
        if (!select) {
            return;
        }
        select.innerHTML = '';
        const defaultOption = document.createElement('option');
        defaultOption.value = '';
        defaultOption.textContent = '默认 (自动)';
        select.appendChild(defaultOption);
        priorities.forEach(item => {
            const option = document.createElement('option');
            option.value = String(item.value ?? '');
            option.textContent = item.label || item.name || item.value;
            select.appendChild(option);
        });
    }

    function updateAccessBadge(access = {}) {
        const badge = qs(selectors.accessBadge);
        if (!badge) {
            return;
        }
        if (access.granted === false) {
            badge.textContent = access.message || '当前账户无权读取系统日志';
            badge.classList.remove('d-none', 'badge-success');
            badge.classList.add('badge', 'bg-warning-subtle', 'text-warning');
        } else {
            badge.textContent = state.isAdmin ? '管理员模式' : '普通模式';
            badge.classList.remove('d-none');
            badge.classList.remove('bg-warning-subtle', 'text-warning');
            badge.classList.add('badge', 'bg-primary-subtle', 'text-primary');
        }
    }

    // -------- Rendering --------
    function renderEntries(entries) {
        const container = qs(selectors.list);
        if (!container) {
            return;
        }
        container.innerHTML = '';
        const fragment = document.createDocumentFragment();
        entries.forEach(entry => {
            const card = document.createElement('div');
            card.className = 'log-entry-card';

            const header = document.createElement('div');
            header.className = 'log-entry-header';
            header.innerHTML = `
                <span class="log-level badge priority-${(entry.priority || '').toLowerCase()}">
                    ${entry.priority || 'UNKNOWN'}
                </span>
                <span class="log-time">${formatDateTime(entry.timestamp)}</span>
            `;

            const body = document.createElement('div');
            body.className = 'log-entry-body';
            body.innerHTML = `
                <div class="log-message">${highlight(entry.message || '', state.keyword)}</div>
            `;

            const footer = document.createElement('div');
            footer.className = 'log-entry-footer';
            footer.innerHTML = `
                <div class="item"><i class="bi bi-hdd-network"></i>${entry.unit || '—'}</div>
                <div class="item"><i class="bi bi-person"></i>${entry.hostname || '—'}</div>
                <div class="item"><i class="bi bi-cpu"></i>${entry.pid || '—'}</div>
            `;

            card.append(header, body, footer);
            fragment.appendChild(card);
        });
        container.appendChild(fragment);
        showList();
    }

    function updateSummary(totalEntries) {
        const summary = qs(selectors.summary);
        if (!summary) {
            return;
        }
        if (state.entries.length === 0) {
            summary.textContent = '未查询到任何日志';
        } else {
            summary.textContent = `已加载 ${state.entries.length} 条日志${state.hasMore ? ' · 可加载更多' : ''}`;
        }
    }

    // -------- Query --------
    function buildQueryParams({ reset = false } = {}) {
        const params = new URLSearchParams();
        const prioritySelect = qs(selectors.priority);
        const keywordInput = qs(selectors.keyword);
        const limitInput = qs(selectors.limit);

        const priority = prioritySelect?.value || '';
        state.keyword = keywordInput?.value?.trim() || '';

        const limitValue = parseInt(limitInput?.value || state.limits.default, 10);
        const clampedLimit = Math.max(1, Math.min(limitValue, state.limits.max));
        params.set('limit', String(clampedLimit));

        if (priority) {
            params.set('priority', priority);
        }
        if (state.keyword) {
            params.set('keyword', state.keyword);
        }

        const sinceInput = qs(selectors.since);
        const untilInput = qs(selectors.until);
        const sinceIso = sinceInput ? isoFromInput(sinceInput) : null;
        const untilIso = untilInput ? isoFromInput(untilInput) : null;
        if (sinceIso) {
            params.set('since', sinceIso);
        }
        if (untilIso) {
            params.set('until', untilIso);
        }
        if (!reset && state.cursor) {
            params.set('cursor', state.cursor);
        }
        return params;
    }

    function loadLogs({ reset = false } = {}) {
        if (state.loading) {
            return;
        }
        if (!state.metadataLoaded) {
            setMessage('日志配置尚未加载，请稍后再试', 'warning');
            return;
        }

        if (reset) {
            state.cursor = null;
            state.entries = [];
            renderEntries([]);
            setMessage('正在加载日志...');
        }

        setLoading(true);
        const params = buildQueryParams({ reset });

        fetch(`/api/logs/query?${params.toString()}`, { credentials: 'include' })
            .then(async response => {
                if (!response.ok) {
                    let detail = `HTTP ${response.status}`;
                    try {
                        const payload = await response.json();
                        if (payload?.detail?.message) {
                            detail = payload.detail.message;
                        } else if (payload?.message) {
                            detail = payload.message;
                        }
                    } catch (err) {
                        /* ignore */
                    }
                    throw new Error(detail);
                }
                return response.json();
            })
            .then(data => {
                const items = Array.isArray(data.entries) ? data.entries : [];
                if (reset) {
                    state.entries = items;
                } else {
                    state.entries = state.entries.concat(items);
                }
                state.hasMore = !!data.has_more;
                state.cursor = data.next_cursor || null;
                renderEntries(state.entries);
                updateSummary(state.entries.length);
                if (!state.entries.length) {
                    setMessage('没有符合条件的日志', 'warning');
                }
                const loadBtn = qs(selectors.loadMoreBtn);
                if (loadBtn) {
                    loadBtn.disabled = !state.hasMore;
                }
            })
            .catch(err => {
                console.error('日志查询失败:', err);
                setMessage(err.message || '日志查询失败', 'error');
            })
            .finally(() => {
                setLoading(false);
            });
    }

    // -------- Events --------
    function bindEvents() {
        handlers.search = () => loadLogs({ reset: true });
        handlers.refresh = () => loadLogs({ reset: true });
        handlers.loadMore = () => {
            if (state.hasMore) {
                loadLogs({ reset: false });
            }
        };
        handlers.reset = () => {
            const elements = [selectors.priority, selectors.keyword, selectors.since, selectors.until, selectors.limit];
            elements.forEach(sel => {
                const el = qs(sel);
                if (!el) {
                    return;
                }
                if (el.tagName === 'SELECT' || el.tagName === 'INPUT') {
                    if (el.type === 'number') {
                        el.value = state.limits.default;
                    } else {
                        el.value = '';
                    }
                }
            });
            loadLogs({ reset: true });
        };

        qs(selectors.searchBtn)?.addEventListener('click', handlers.search);
        qs(selectors.refreshBtn)?.addEventListener('click', handlers.refresh);
        qs(selectors.loadMoreBtn)?.addEventListener('click', handlers.loadMore);
        qs(selectors.resetBtn)?.addEventListener('click', handlers.reset);
    }

    function unbindEvents() {
        if (handlers.search) {
            qs(selectors.searchBtn)?.removeEventListener('click', handlers.search);
        }
        if (handlers.refresh) {
            qs(selectors.refreshBtn)?.removeEventListener('click', handlers.refresh);
        }
        if (handlers.loadMore) {
            qs(selectors.loadMoreBtn)?.removeEventListener('click', handlers.loadMore);
        }
        if (handlers.reset) {
            qs(selectors.resetBtn)?.removeEventListener('click', handlers.reset);
        }
    }

    function handleAdminModeChange(event) {
        if (!event || typeof event.detail !== 'object') {
            return;
        }
        state.isAdmin = !!event.detail.active;
        fetchMetadata().then(() => {
            loadLogs({ reset: true });
        });
    }

    // -------- Module lifecycle --------
    window.moduleInit = function () {
        bindEvents();
        fetchMetadata().then(() => {
            loadLogs({ reset: true });
        });
        adminModeListener = handleAdminModeChange.bind(null);
        window.addEventListener('rosdeck:admin-mode-change', adminModeListener);
    };

    window.moduleCleanup = function () {
        unbindEvents();
        if (adminModeListener) {
            window.removeEventListener('rosdeck:admin-mode-change', adminModeListener);
            adminModeListener = null;
        }
        state.cursor = null;
        state.entries = [];
        state.hasMore = false;
        state.loading = false;
        state.metadataLoaded = false;
    };
})();
