/**
 * ROS 概览模块
 * WebSocket实时监控ROS图谱变化
 */

(function() {
    let ws = null;
    let reconnectTimer = null;
    let heartbeatTimer = null;

    // 数据存储
    const state = {
        nodes: new Map(),
        topics: new Map(),
        services: new Map(),
        bookmarks: {
            nodes: new Set(),
            topics: new Set(),
            services: new Set()
        },
        expandedTopics: new Set(), // 展开的话题详情
        searchFilters: {
            nodes: '',
            topics: '',
            services: ''
        }
    };

    /**
     * 模块初始化
     */
    window.moduleInit = function() {
        console.log('ROS 概览模块初始化');
        initEventListeners();
        connectWebSocket();
    };

    /**
     * 模块清理
     */
    window.moduleCleanup = function() {
        console.log('ROS 概览模块清理');
        disconnectWebSocket();
        clearTimeout(reconnectTimer);
        clearInterval(heartbeatTimer);
    };

    /**
     * 初始化事件监听
     */
    function initEventListeners() {
        // 搜索框
        $('#nodes-search').on('input', (e) => {
            state.searchFilters.nodes = e.target.value.toLowerCase();
            renderNodes();
        });

        $('#topics-search').on('input', (e) => {
            state.searchFilters.topics = e.target.value.toLowerCase();
            renderTopics();
        });

        $('#services-search').on('input', (e) => {
            state.searchFilters.services = e.target.value.toLowerCase();
            renderServices();
        });
    }

    /**
     * WebSocket连接
     */
    function connectWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/ros/ws`;

        console.log('连接 WebSocket:', wsUrl);
        updateConnectionStatus('connecting');

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log('WebSocket 已连接');
            updateConnectionStatus('connected');
            startHeartbeat();
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleMessage(data);
            } catch (e) {
                console.error('解析 WebSocket 消息失败:', e);
            }
        };

        ws.onerror = (error) => {
            console.error('WebSocket 错误:', error);
            updateConnectionStatus('error');
        };

        ws.onclose = () => {
            console.log('WebSocket 已断开');
            updateConnectionStatus('disconnected');
            stopHeartbeat();
            // 5秒后重连
            reconnectTimer = setTimeout(() => {
                console.log('尝试重新连接...');
                connectWebSocket();
            }, 5000);
        };
    }

    /**
     * 断开 WebSocket
     */
    function disconnectWebSocket() {
        if (ws) {
            ws.close();
            ws = null;
        }
    }

    /**
     * 启动心跳
     */
    function startHeartbeat() {
        heartbeatTimer = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'pong' }));
            }
        }, 30000); // 30秒心跳
    }

    /**
     * 停止心跳
     */
    function stopHeartbeat() {
        if (heartbeatTimer) {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
    }

    /**
     * 处理 WebSocket 消息
     */
    function handleMessage(data) {
        console.log('收到消息:', data.type);

        switch (data.type) {
            case 'full':
                handleFullData(data);
                break;
            case 'delta':
                handleDeltaData(data);
                break;
            case 'topic_details':
                handleTopicDetails(data);
                break;
            case 'bookmark_updated':
                handleBookmarkUpdated(data);
                break;
            case 'ping':
                // 心跳
                ws.send(JSON.stringify({ type: 'pong' }));
                break;
            case 'error':
                toastr.error(data.message || '发生错误');
                break;
            default:
                console.warn('未知消息类型:', data.type);
        }
    }

    /**
     * 处理全量数据
     */
    function handleFullData(data) {
        console.log('处理全量数据');

        // 更新系统信息
        if (data.system_info) {
            $('#ros-version').text(data.system_info.ros_version || '-');
            $('#dds-impl').text(data.system_info.dds_impl || '-');
            $('#domain-id').text(data.system_info.domain_id || '-');
        }

        // 更新节点
        state.nodes.clear();
        (data.nodes || []).forEach(node => {
            state.nodes.set(node.full_name, node);
        });

        // 更新话题
        state.topics.clear();
        (data.topics || []).forEach(topic => {
            state.topics.set(topic.name, topic);
        });

        // 更新服务
        state.services.clear();
        (data.services || []).forEach(service => {
            state.services.set(service.name, service);
        });

        // 更新标记
        if (data.bookmarks) {
            state.bookmarks.nodes = new Set(data.bookmarks.nodes || []);
            state.bookmarks.topics = new Set(data.bookmarks.topics || []);
            state.bookmarks.services = new Set(data.bookmarks.services || []);
        }

        // 更新时间
        updateLastUpdateTime(data.timestamp);

        // 渲染
        renderAll();
    }

    /**
     * 处理增量数据
     */
    function handleDeltaData(data) {
        console.log('处理增量数据');

        // 添加节点
        if (data.added_nodes) {
            data.added_nodes.forEach(node => {
                state.nodes.set(node.full_name, node);
            });
        }

        // 删除节点
        if (data.removed_nodes) {
            data.removed_nodes.forEach(nodeName => {
                state.nodes.delete(nodeName);
            });
        }

        // 添加话题
        if (data.added_topics) {
            data.added_topics.forEach(topic => {
                state.topics.set(topic.name, topic);
            });
        }

        // 删除话题
        if (data.removed_topics) {
            data.removed_topics.forEach(topicName => {
                state.topics.delete(topicName);
                state.expandedTopics.delete(topicName);
            });
        }

        // 更新话题
        if (data.updated_topics) {
            data.updated_topics.forEach(topic => {
                state.topics.set(topic.name, topic);
            });
        }

        // 添加服务
        if (data.added_services) {
            data.added_services.forEach(service => {
                state.services.set(service.name, service);
            });
        }

        // 删除服务
        if (data.removed_services) {
            data.removed_services.forEach(serviceName => {
                state.services.delete(serviceName);
            });
        }

        // 更新时间
        updateLastUpdateTime(data.timestamp);

        // 渲染
        renderAll();
    }

    /**
     * 处理话题详情
     */
    function handleTopicDetails(data) {
        const topicName = data.topic;
        const details = data.details;

        if (details && state.topics.has(topicName)) {
            // 更新话题数据
            const topic = state.topics.get(topicName);
            topic.publishers = details.publishers || [];
            topic.subscribers = details.subscribers || [];
            state.topics.set(topicName, topic);

            // 重新渲染话题详情
            renderTopicDetails(topicName);
        }
    }

    /**
     * 处理标记更新
     */
    function handleBookmarkUpdated(data) {
        if (data.bookmarks) {
            state.bookmarks.nodes = new Set(data.bookmarks.nodes || []);
            state.bookmarks.topics = new Set(data.bookmarks.topics || []);
            state.bookmarks.services = new Set(data.bookmarks.services || []);

            // 重新渲染
            renderAll();
            toastr.success('标记已更新');
        }
    }

    /**
     * 渲染所有内容
     */
    function renderAll() {
        renderNodes();
        renderTopics();
        renderServices();
        updateCounts();
    }

    /**
     * 更新计数
     */
    function updateCounts() {
        $('#nodes-count').text(state.nodes.size);
        $('#topics-count').text(state.topics.size);
        $('#services-count').text(state.services.size);
    }

    /**
     * 渲染节点列表
     */
    function renderNodes() {
        const container = $('#nodes-list');
        const filter = state.searchFilters.nodes;

        // 过滤和排序
        let nodes = Array.from(state.nodes.values());

        if (filter) {
            nodes = nodes.filter(node =>
                node.full_name.toLowerCase().includes(filter) ||
                node.namespace.toLowerCase().includes(filter)
            );
        }

        // 置顶标记的节点
        nodes.sort((a, b) => {
            const aBookmarked = state.bookmarks.nodes.has(a.full_name);
            const bBookmarked = state.bookmarks.nodes.has(b.full_name);
            if (aBookmarked && !bBookmarked) return -1;
            if (!aBookmarked && bBookmarked) return 1;
            return a.full_name.localeCompare(b.full_name);
        });

        if (nodes.length === 0) {
            container.html(`
                <div class="empty-state">
                    <i class="bi bi-inbox"></i>
                    <p>${filter ? '未找到匹配的节点' : '暂无节点数据'}</p>
                </div>
            `);
            return;
        }

        const html = nodes.map(node => {
            const isBookmarked = state.bookmarks.nodes.has(node.full_name);
            const runtime = calculateRuntime(node.first_seen);

            return `
                <div class="entity-card ${isBookmarked ? 'bookmarked' : ''}">
                    <div class="entity-header">
                        <div class="entity-name">
                            ${isBookmarked ? '<i class="bi bi-star-fill" style="color: #ffc107; margin-right: 5px;"></i>' : ''}
                            ${escapeHtml(node.full_name)}
                        </div>
                        <div class="entity-actions">
                            <button class="btn-icon ${isBookmarked ? 'bookmarked' : ''}"
                                    onclick="window.toggleBookmark('nodes', '${escapeHtml(node.full_name)}')"
                                    title="${isBookmarked ? '取消标记' : '标记'}">
                                <i class="bi bi-star${isBookmarked ? '-fill' : ''}"></i>
                            </button>
                        </div>
                    </div>
                    <div class="entity-meta">
                        <div class="meta-item">
                            <i class="bi bi-diagram-2"></i>
                            <span>${escapeHtml(node.namespace)}</span>
                        </div>
                        <div class="meta-item">
                            <i class="bi bi-clock"></i>
                            <span>运行时长: ${runtime}</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        container.html(html);
    }

    /**
     * 渲染话题列表
     */
    function renderTopics() {
        const container = $('#topics-list');
        const filter = state.searchFilters.topics;

        // 过滤和排序
        let topics = Array.from(state.topics.values());

        if (filter) {
            topics = topics.filter(topic =>
                topic.name.toLowerCase().includes(filter) ||
                topic.type.toLowerCase().includes(filter)
            );
        }

        // 置顶标记的话题
        topics.sort((a, b) => {
            const aBookmarked = state.bookmarks.topics.has(a.name);
            const bBookmarked = state.bookmarks.topics.has(b.name);
            if (aBookmarked && !bBookmarked) return -1;
            if (!aBookmarked && bBookmarked) return 1;
            return a.name.localeCompare(b.name);
        });

        if (topics.length === 0) {
            container.html(`
                <div class="empty-state">
                    <i class="bi bi-inbox"></i>
                    <p>${filter ? '未找到匹配的话题' : '暂无话题数据'}</p>
                </div>
            `);
            return;
        }

        const html = topics.map(topic => {
            const isBookmarked = state.bookmarks.topics.has(topic.name);
            const isExpanded = state.expandedTopics.has(topic.name);

            return `
                <div class="entity-card ${isBookmarked ? 'bookmarked' : ''}">
                    <div class="entity-header">
                        <div class="entity-name">
                            ${isBookmarked ? '<i class="bi bi-star-fill" style="color: #ffc107; margin-right: 5px;"></i>' : ''}
                            ${escapeHtml(topic.name)}
                        </div>
                        <div class="entity-actions">
                            <button class="btn-icon ${isBookmarked ? 'bookmarked' : ''}"
                                    onclick="window.toggleBookmark('topics', '${escapeHtml(topic.name)}')"
                                    title="${isBookmarked ? '取消标记' : '标记'}">
                                <i class="bi bi-star${isBookmarked ? '-fill' : ''}"></i>
                            </button>
                            <button class="btn-icon"
                                    onclick="window.toggleTopicDetails('${escapeHtml(topic.name)}')"
                                    title="${isExpanded ? '收起详情' : '展开详情'}">
                                <i class="bi bi-chevron-${isExpanded ? 'up' : 'down'}"></i>
                            </button>
                        </div>
                    </div>
                    <div class="entity-meta">
                        <div class="meta-item">
                            <i class="bi bi-file-earmark-code"></i>
                            <span>${escapeHtml(topic.type)}</span>
                        </div>
                        <div class="meta-item">
                            <i class="bi bi-arrow-up"></i>
                            <span>${topic.publisher_count} 发布者</span>
                        </div>
                        <div class="meta-item">
                            <i class="bi bi-arrow-down"></i>
                            <span>${topic.subscriber_count} 订阅者</span>
                        </div>
                    </div>
                    <div class="topic-details" id="topic-details-${sanitizeId(topic.name)}" style="display: ${isExpanded ? 'block' : 'none'};">
                        ${isExpanded ? renderTopicDetailsContent(topic) : ''}
                    </div>
                </div>
            `;
        }).join('');

        container.html(html);
    }

    /**
     * 渲染话题详情内容
     */
    function renderTopicDetailsContent(topic) {
        if (!topic.publishers && !topic.subscribers) {
            return '<div class="topic-details-loading">加载中...</div>';
        }

        const publishers = topic.publishers || [];
        const subscribers = topic.subscribers || [];

        return `
            <div class="topic-participants">
                <div class="participants-group">
                    <h6>发布者 (${publishers.length})</h6>
                    <ul class="participants-list">
                        ${publishers.length > 0 ? publishers.map(pub => `
                            <li><i class="bi bi-node-plus"></i> ${escapeHtml(pub)}</li>
                        `).join('') : '<li style="color: #6c757d;">无</li>'}
                    </ul>
                </div>
                <div class="participants-group">
                    <h6>订阅者 (${subscribers.length})</h6>
                    <ul class="participants-list">
                        ${subscribers.length > 0 ? subscribers.map(sub => `
                            <li><i class="bi bi-node-minus"></i> ${escapeHtml(sub)}</li>
                        `).join('') : '<li style="color: #6c757d;">无</li>'}
                    </ul>
                </div>
            </div>
        `;
    }

    /**
     * 渲染话题详情（单个）
     */
    function renderTopicDetails(topicName) {
        const topic = state.topics.get(topicName);
        if (!topic) return;

        const detailsContainer = $(`#topic-details-${sanitizeId(topicName)}`);
        if (detailsContainer.length > 0) {
            detailsContainer.html(renderTopicDetailsContent(topic));
        }
    }

    /**
     * 渲染服务列表
     */
    function renderServices() {
        const container = $('#services-list');
        const filter = state.searchFilters.services;

        // 过滤和排序
        let services = Array.from(state.services.values());

        if (filter) {
            services = services.filter(service =>
                service.name.toLowerCase().includes(filter) ||
                service.type.toLowerCase().includes(filter)
            );
        }

        // 置顶标记的服务
        services.sort((a, b) => {
            const aBookmarked = state.bookmarks.services.has(a.name);
            const bBookmarked = state.bookmarks.services.has(b.name);
            if (aBookmarked && !bBookmarked) return -1;
            if (!aBookmarked && bBookmarked) return 1;
            return a.name.localeCompare(b.name);
        });

        if (services.length === 0) {
            container.html(`
                <div class="empty-state">
                    <i class="bi bi-inbox"></i>
                    <p>${filter ? '未找到匹配的服务' : '暂无服务数据'}</p>
                </div>
            `);
            return;
        }

        const html = services.map(service => {
            const isBookmarked = state.bookmarks.services.has(service.name);

            return `
                <div class="entity-card ${isBookmarked ? 'bookmarked' : ''}">
                    <div class="entity-header">
                        <div class="entity-name">
                            ${isBookmarked ? '<i class="bi bi-star-fill" style="color: #ffc107; margin-right: 5px;"></i>' : ''}
                            ${escapeHtml(service.name)}
                        </div>
                        <div class="entity-actions">
                            <button class="btn-icon ${isBookmarked ? 'bookmarked' : ''}"
                                    onclick="window.toggleBookmark('services', '${escapeHtml(service.name)}')"
                                    title="${isBookmarked ? '取消标记' : '标记'}">
                                <i class="bi bi-star${isBookmarked ? '-fill' : ''}"></i>
                            </button>
                        </div>
                    </div>
                    <div class="entity-meta">
                        <div class="meta-item">
                            <i class="bi bi-file-earmark-code"></i>
                            <span>${escapeHtml(service.type)}</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        container.html(html);
    }

    /**
     * 切换标记
     */
    window.toggleBookmark = function(entityType, entityName) {
        const isBookmarked = state.bookmarks[entityType].has(entityName);
        const action = isBookmarked ? 'remove' : 'add';

        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'bookmark',
                entity_type: entityType,
                entity_name: entityName,
                action: action
            }));
        }
    };

    /**
     * 切换话题详情
     */
    window.toggleTopicDetails = function(topicName) {
        const isExpanded = state.expandedTopics.has(topicName);

        if (isExpanded) {
            // 收起
            state.expandedTopics.delete(topicName);
            $(`#topic-details-${sanitizeId(topicName)}`).slideUp();
        } else {
            // 展开
            state.expandedTopics.add(topicName);
            const detailsContainer = $(`#topic-details-${sanitizeId(topicName)}`);
            detailsContainer.slideDown();

            // 如果没有详细数据，请求获取
            const topic = state.topics.get(topicName);
            if (topic && !topic.publishers && !topic.subscribers) {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: 'get_topic_details',
                        topic: topicName
                    }));
                }
            }
        }
    };

    /**
     * 更新连接状态
     */
    function updateConnectionStatus(status) {
        const indicator = $('#ws-status-indicator');
        const text = $('#ws-status-text');

        switch (status) {
            case 'connecting':
                indicator.removeClass('connected disconnected');
                text.text('连接中...');
                break;
            case 'connected':
                indicator.removeClass('disconnected').addClass('connected');
                text.text('已连接');
                break;
            case 'disconnected':
            case 'error':
                indicator.removeClass('connected').addClass('disconnected');
                text.text('已断开');
                break;
        }
    }

    /**
     * 更新最后更新时间
     */
    function updateLastUpdateTime(timestamp) {
        if (timestamp) {
            const date = new Date(timestamp);
            $('#last-update').text(date.toLocaleString('zh-CN'));
        }
    }

    /**
     * 计算运行时长
     */
    function calculateRuntime(firstSeenStr) {
        if (!firstSeenStr) return '-';

        const firstSeen = new Date(firstSeenStr);
        const now = new Date();
        const diffMs = now - firstSeen;

        const seconds = Math.floor(diffMs / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);

        if (days > 0) return `${days}天 ${hours % 24}小时`;
        if (hours > 0) return `${hours}小时 ${minutes % 60}分钟`;
        if (minutes > 0) return `${minutes}分钟`;
        return `${seconds}秒`;
    }

    /**
     * HTML转义
     */
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * ID净化（用于DOM ID）
     */
    function sanitizeId(str) {
        return str.replace(/[^a-zA-Z0-9_-]/g, '_');
    }

})();
