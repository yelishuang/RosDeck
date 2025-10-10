/**
 * 存储管理模块 API 封装
 */

(() => {
    const BASE_URL = '/api/storage';

    function getCsrfToken() {
        const fromSession = sessionStorage.getItem('rosdeck_csrf_token');
        if (fromSession) {
            return fromSession;
        }
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') || '' : '';
    }

    async function fetchJson(url, options = {}) {
        const requestOptions = {
            credentials: 'include',
            ...options,
        };
        const response = await fetch(url, requestOptions);
        let data = null;
        try {
            data = await response.json();
        } catch (err) {
            // ignore JSON parse error for non JSON response
        }
        if (!response.ok) {
            const error = new Error(data?.message || `请求失败 (HTTP ${response.status})`);
            error.response = response;
            error.data = data;
            throw error;
        }
        return data;
    }

    async function postJson(url, payload) {
        const csrf = getCsrfToken();
        const headers = {
            'Content-Type': 'application/json',
            ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
        };
        return fetchJson(url, {
            method: 'POST',
            headers,
            body: JSON.stringify(payload || {}),
        });
    }

    const StorageApi = {
        async summary() {
            return fetchJson(`${BASE_URL}/summary`);
        },
        async partitions() {
            return fetchJson(`${BASE_URL}/partitions`);
        },
        async report(format = 'json') {
            return fetchJson(`${BASE_URL}/report?format=${format}`);
        },
        async downloadReport(format = 'csv') {
            const response = await fetch(`${BASE_URL}/report?format=${format}`, {
                credentials: 'include',
            });
            if (!response.ok) {
                const text = await response.text();
                const error = new Error(`导出失败 (HTTP ${response.status})`);
                error.detail = text;
                throw error;
            }
            return response;
        },
        async cleanup(payload) {
            return postJson(`${BASE_URL}/cleanup`, payload);
        },
        async mount(payload) {
            return postJson(`${BASE_URL}/mount`, payload);
        },
        async partition(payload) {
            return postJson(`${BASE_URL}/partition`, payload);
        },
        async smartSelftest(payload) {
            return postJson(`${BASE_URL}/smart-selftest`, payload);
        },
        async smartReport(device) {
            const csrf = getCsrfToken();
            const headers = csrf ? { 'X-CSRF-Token': csrf } : {};
            return fetchJson(`${BASE_URL}/smart-report?device=${encodeURIComponent(device)}`, {
                headers,
            });
        },
        async operations() {
            const csrf = getCsrfToken();
            const headers = csrf ? { 'X-CSRF-Token': csrf } : {};
            return fetchJson(`${BASE_URL}/operations`, {
                headers,
            });
        },
    };

    window.StorageApi = StorageApi;
})();
