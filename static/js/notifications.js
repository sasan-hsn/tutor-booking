/**
 * notifications.js - Real-time notification bell, dropdown, toast alerts,
 * and resilient WebSocket client with exponential backoff auto-reconnect.
 */

(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NotificationsClient = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const RECONNECT_INTERVALS = [1000, 2000, 5000, 10000];

    function getCsrfToken() {
        let cookieValue = null;
        if (typeof document !== 'undefined' && document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, 10) === 'csrftoken=') {
                    cookieValue = decodeURIComponent(cookie.substring(10));
                    break;
                }
            }
        }
        return cookieValue;
    }

    function getLevelIconHtml(level) {
        switch (level) {
            case 'success':
                return '<i class="bi bi-check-circle-fill text-success fs-5"></i>';
            case 'warning':
                return '<i class="bi bi-exclamation-triangle-fill text-warning fs-5"></i>';
            case 'info':
            default:
                return '<i class="bi bi-info-circle-fill text-primary fs-5"></i>';
        }
    }

    function getWebSocketUrl(path = '/ws/notifications/') {
        if (typeof window === 'undefined') return '';
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${window.location.host}${path}`;
    }

    function getCurrentUnreadCount(elements = {}) {
        const badgeCount = elements.badgeCount || (typeof document !== 'undefined' ? document.getElementById('notificationBadgeCount') : null);
        if (badgeCount) {
            return parseInt(badgeCount.textContent, 10) || 0;
        }
        return 0;
    }

    function updateUnreadCount(count, elements = {}) {
        const n = Math.max(0, parseInt(count, 10) || 0);

        const badge = elements.badge || (typeof document !== 'undefined' ? document.getElementById('notificationBadge') : null);
        const badgeCount = elements.badgeCount || (typeof document !== 'undefined' ? document.getElementById('notificationBadgeCount') : null);
        const dropdownCountBadge = elements.dropdownCountBadge || (typeof document !== 'undefined' ? document.getElementById('notificationDropdownCountBadge') : null);
        const markAllBtn = elements.markAllBtn || (typeof document !== 'undefined' ? document.getElementById('markAllReadBtn') : null);

        if (badgeCount) {
            badgeCount.textContent = String(n);
        }

        if (badge) {
            if (n === 0) {
                badge.classList.add('d-none');
            } else {
                badge.classList.remove('d-none');
            }
        }

        if (dropdownCountBadge) {
            dropdownCountBadge.textContent = `${n} unread`;
        }

        if (markAllBtn) {
            markAllBtn.disabled = (n === 0);
        }
    }

    function updateUnreadChatCount(count, elements = {}) {
        const n = Math.max(0, parseInt(count, 10) || 0);

        const chatBadge = elements.chatBadge || (typeof document !== 'undefined' ? document.getElementById('chatBadge') : null);
        const chatBadgeCount = elements.chatBadgeCount || (typeof document !== 'undefined' ? document.getElementById('chatBadgeCount') : null);

        if (chatBadgeCount) {
            chatBadgeCount.textContent = String(n);
        }

        if (chatBadge) {
            if (n === 0) {
                chatBadge.classList.add('d-none');
            } else {
                chatBadge.classList.remove('d-none');
            }
        }
    }

    function prependNotificationItem(data, listEl) {
        if (!listEl || typeof document === 'undefined') return null;

        const emptyMsg = listEl.querySelector('#noNotificationsMessage');
        if (emptyMsg) {
            emptyMsg.remove();
        }

        // Check if item already exists
        const existing = listEl.querySelector(`.notification-item[data-id="${data.id}"]`);
        if (existing) {
            return existing;
        }

        const item = document.createElement('div');
        item.className = 'notification-item p-3 border-bottom d-flex gap-3 align-items-start notification-unread';
        item.dataset.id = String(data.id);
        if (data.action_url) {
            item.dataset.actionUrl = data.action_url;
        }
        item.setAttribute('role', 'button');
        item.setAttribute('tabindex', '0');

        const iconWrapper = document.createElement('div');
        iconWrapper.className = 'notification-icon-wrapper flex-shrink-0 mt-1';
        iconWrapper.innerHTML = getLevelIconHtml(data.level);
        item.appendChild(iconWrapper);

        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'flex-grow-1 min-w-0';

        const headerRow = document.createElement('div');
        headerRow.className = 'd-flex align-items-center justify-content-between gap-2 mb-1';

        const titleEl = document.createElement('span');
        titleEl.className = 'fw-semibold text-truncate small text-dark';
        titleEl.textContent = data.title;
        headerRow.appendChild(titleEl);

        const timeEl = document.createElement('small');
        timeEl.className = 'text-muted text-nowrap notification-time';
        timeEl.style.fontSize = '0.75rem';
        timeEl.textContent = 'Just now';
        headerRow.appendChild(timeEl);

        contentWrapper.appendChild(headerRow);

        const messageEl = document.createElement('p');
        messageEl.className = 'mb-0 text-muted small text-break';
        messageEl.textContent = data.message;
        contentWrapper.appendChild(messageEl);

        item.appendChild(contentWrapper);

        const dot = document.createElement('span');
        dot.className = 'notification-dot bg-primary rounded-circle flex-shrink-0 mt-2';
        dot.setAttribute('title', 'Unread');
        item.appendChild(dot);

        // Prepend as first child
        if (listEl.firstChild) {
            listEl.insertBefore(item, listEl.firstChild);
        } else {
            listEl.appendChild(item);
        }

        // Enforce 20-item cap
        const items = listEl.querySelectorAll('.notification-item');
        if (items.length > 20) {
            items[items.length - 1].remove();
        }

        return item;
    }

    function showNotificationToast(data, containerEl, options = {}) {
        if (!containerEl || typeof document === 'undefined') return null;
        const elements = options.elements || {};

        const toastEl = document.createElement('div');
        toastEl.className = 'toast align-items-center border-0 shadow notification-toast';
        toastEl.setAttribute('role', 'alert');
        toastEl.setAttribute('aria-live', 'assertive');
        toastEl.setAttribute('aria-atomic', 'true');

        const flexRow = document.createElement('div');
        flexRow.className = 'd-flex';

        const toastBody = document.createElement('div');
        toastBody.className = 'toast-body d-flex gap-3 align-items-start flex-grow-1';
        if (data.action_url) {
            toastBody.style.cursor = 'pointer';
        }

        const iconWrapper = document.createElement('div');
        iconWrapper.className = 'notification-icon-wrapper flex-shrink-0 mt-1';
        iconWrapper.innerHTML = getLevelIconHtml(data.level);
        toastBody.appendChild(iconWrapper);

        const textWrapper = document.createElement('div');
        textWrapper.className = 'flex-grow-1 min-w-0';

        const titleEl = document.createElement('strong');
        titleEl.className = 'd-block mb-1 text-dark small text-truncate';
        titleEl.textContent = data.title;
        textWrapper.appendChild(titleEl);

        const msgEl = document.createElement('div');
        msgEl.className = 'text-muted small text-break';
        msgEl.textContent = data.message;
        textWrapper.appendChild(msgEl);

        toastBody.appendChild(textWrapper);
        flexRow.appendChild(toastBody);

        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'btn-close me-2 m-auto';
        closeBtn.setAttribute('data-bs-dismiss', 'toast');
        closeBtn.setAttribute('aria-label', 'Close');
        flexRow.appendChild(closeBtn);

        toastEl.appendChild(flexRow);
        containerEl.appendChild(toastEl);

        // Click handler on toast body to navigate and mark as read
        if (data.action_url) {
            toastBody.addEventListener('click', async (e) => {
                // If close button was clicked, don't navigate
                if (e.target && e.target.closest('.btn-close')) return;
                try {
                    await markNotificationAsRead(data.id, { elements });
                } finally {
                    if (typeof window !== 'undefined') {
                        window.location.href = data.action_url;
                    }
                }
            });
        }

        // Initialize Bootstrap Toast if available
        if (typeof window !== 'undefined' && window.bootstrap && typeof window.bootstrap.Toast === 'function') {
            const toast = new window.bootstrap.Toast(toastEl, { delay: 6000, autohide: true });
            toast.show();
            toastEl.addEventListener('hidden.bs.toast', () => {
                toastEl.remove();
            });
        }

        return toastEl;
    }

    function markNotificationItemReadInDom(id, listEl) {
        if (!id) return;
        const container = listEl || (typeof document !== 'undefined' ? document : null);
        if (!container) return;
        const item = container.querySelector(`.notification-item[data-id="${id}"]`);
        if (item) {
            item.classList.remove('notification-unread', 'bg-light-subtle');
            const dot = item.querySelector('.notification-dot');
            if (dot) dot.remove();
        }
    }

    function handleNotificationReadEvent(payload, options = {}) {
        const data = (payload && payload.detail) ? payload.detail : payload;
        if (!data) return;
        const elements = options.elements || {};
        const listEl = options.listEl || elements.list || (typeof document !== 'undefined' ? document.getElementById('notificationList') : null);

        if (typeof data.unread_count !== 'undefined') {
            updateUnreadCount(data.unread_count, elements);
        }

        if (Array.isArray(data.notification_ids)) {
            data.notification_ids.forEach(id => {
                markNotificationItemReadInDom(id, listEl);
            });
        }
    }

    async function markNotificationAsRead(id, options = {}) {
        if (!id) return;
        const elements = options.elements || {};
        const url = `/notifications/${id}/read/`;

        // Update UI immediately (optimistic update)
        markNotificationItemReadInDom(id, elements.list);

        try {
            const response = await fetch(url, {
                method: 'POST',
                keepalive: true,
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken() || '',
                },
            });

            if (response.ok) {
                const data = await response.json();
                if (data && typeof data.unread_count !== 'undefined') {
                    updateUnreadCount(data.unread_count, elements);
                }
            }
        } catch (err) {
            console.error('Failed to mark notification as read:', err);
        }
    }

    async function markAllNotificationsAsRead(options = {}) {
        const elements = options.elements || {};
        const list = options.list || (typeof document !== 'undefined' ? document.getElementById('notificationList') : null);
        const url = '/notifications/mark-all-read/';

        // Optimistically clear unread indicators
        if (list) {
            const unreadItems = list.querySelectorAll('.notification-unread');
            unreadItems.forEach(item => {
                item.classList.remove('notification-unread', 'bg-light-subtle');
            });
            const dots = list.querySelectorAll('.notification-dot');
            dots.forEach(dot => dot.remove());
        }
        updateUnreadCount(0, elements);

        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken() || '',
                },
            });

            if (response.ok) {
                const data = await response.json();
                if (data && typeof data.unread_count !== 'undefined') {
                    updateUnreadCount(data.unread_count, elements);
                }
            }
        } catch (err) {
            console.error('Failed to mark all notifications as read:', err);
        }
    }

    async function handleNotificationClick(itemEl, options = {}) {
        if (!itemEl) return;
        const id = itemEl.dataset.id;
        const actionUrl = itemEl.dataset.actionUrl;
        const windowObj = options.windowObj || (typeof window !== 'undefined' ? window : null);
        const elements = options.elements || {};

        if (itemEl.classList.contains('notification-unread')) {
            itemEl.classList.remove('notification-unread', 'bg-light-subtle');
            const dot = itemEl.querySelector('.notification-dot');
            if (dot) dot.remove();
            try {
                await markNotificationAsRead(id, { elements });
            } catch (err) {
                console.error('Failed marking notification read on click:', err);
            }
        }

        if (actionUrl && windowObj) {
            windowObj.location.href = actionUrl;
        }
    }

    function connectNotificationWebSocket(options = {}) {
        const WebSocketClass = options.WebSocketClass || (typeof window !== 'undefined' ? window.WebSocket : null);
        if (!WebSocketClass) return null;

        const url = options.url || getWebSocketUrl('/ws/notifications/');
        const intervals = options.reconnectIntervals || RECONNECT_INTERVALS;
        const elements = options.elements || {};
        const listEl = elements.list || (typeof document !== 'undefined' ? document.getElementById('notificationList') : null);
        const toastContainerEl = elements.toastContainer || (typeof document !== 'undefined' ? document.getElementById('notificationToastContainer') : null);

        let reconnectAttempts = 0;
        let reconnectTimer = null;
        let socket = null;
        let isExplicitlyClosed = false;

        function connect() {
            if (isExplicitlyClosed) return;

            try {
                socket = new WebSocketClass(url);
            } catch (err) {
                console.error('WebSocket initialization error:', err);
                scheduleReconnect();
                return;
            }

            socket.onopen = function () {
                reconnectAttempts = 0;
            };

            socket.onmessage = function (event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data && data.type === 'notification_message') {
                        const newCount = typeof data.unread_count !== 'undefined'
                            ? data.unread_count
                            : getCurrentUnreadCount(elements) + 1;

                        updateUnreadCount(newCount, elements);

                        if (listEl) {
                            prependNotificationItem(data, listEl);
                        }
                        if (toastContainerEl) {
                            showNotificationToast(data, toastContainerEl, { elements });
                        }

                        if (typeof document !== 'undefined' && typeof document.dispatchEvent === 'function') {
                            document.dispatchEvent(new CustomEvent('tutor:notification', { detail: data }));
                        }
                    } else if (data && data.type === 'notification_read') {
                        handleNotificationReadEvent(data, { elements, listEl });

                        if (typeof document !== 'undefined' && typeof document.dispatchEvent === 'function') {
                            document.dispatchEvent(new CustomEvent('tutor:notification:read', { detail: data }));
                        }
                    } else if (data && data.type === 'unread_chat_count') {
                        const count = typeof data.count !== 'undefined' ? data.count : 0;
                        updateUnreadChatCount(count, elements);

                        if (typeof document !== 'undefined' && typeof document.dispatchEvent === 'function') {
                            document.dispatchEvent(new CustomEvent('tutor:chat:unread_count', { detail: data }));
                        }
                    }
                } catch (err) {
                    console.error('Error handling notification message:', err);
                }
            };

            socket.onclose = function () {
                if (!isExplicitlyClosed) {
                    scheduleReconnect();
                }
            };

            socket.onerror = function () {
                // onclose triggers reconnect
            };
        }

        function scheduleReconnect() {
            if (isExplicitlyClosed) return;
            const delay = intervals[Math.min(reconnectAttempts, intervals.length - 1)];
            reconnectAttempts++;
            if (reconnectTimer) clearTimeout(reconnectTimer);
            reconnectTimer = setTimeout(connect, delay);
        }

        connect();

        return {
            get socket() {
                return socket;
            },
            disconnect: function () {
                isExplicitlyClosed = true;
                if (reconnectTimer) {
                    clearTimeout(reconnectTimer);
                    reconnectTimer = null;
                }
                if (socket) {
                    socket.close();
                }
            },
        };
    }

    function initNotifications(options = {}) {
        if (typeof document === 'undefined') return null;

        const dropdownBtn = document.getElementById('notificationDropdownBtn');
        const chatBtn = document.getElementById('chatInboxBtn');
        if (!dropdownBtn && !chatBtn && !options.force) return null;

        const elements = {
            badge: document.getElementById('notificationBadge'),
            badgeCount: document.getElementById('notificationBadgeCount'),
            dropdownCountBadge: document.getElementById('notificationDropdownCountBadge'),
            markAllBtn: document.getElementById('markAllReadBtn'),
            list: document.getElementById('notificationList'),
            toastContainer: document.getElementById('notificationToastContainer'),
            chatBadge: document.getElementById('chatBadge'),
            chatBadgeCount: document.getElementById('chatBadgeCount'),
        };

        // Attach Mark All as Read button listener
        if (elements.markAllBtn) {
            elements.markAllBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                markAllNotificationsAsRead({ list: elements.list, elements });
            });
        }

        // Attach delegated listener on notification list for item clicks and keyboard activation
        if (elements.list) {
            elements.list.addEventListener('click', (e) => {
                const item = e.target.closest('.notification-item');
                if (item) {
                    e.preventDefault();
                    handleNotificationClick(item, { elements });
                }
            });

            elements.list.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    const item = e.target.closest('.notification-item');
                    if (item) {
                        e.preventDefault();
                        handleNotificationClick(item, { elements });
                    }
                }
            });
        }

        // Attach listener for tutor:notification:read DOM events to reconcile UI
        if (typeof document !== 'undefined' && typeof document.addEventListener === 'function' && !document._notificationReadListenerAttached) {
            document.addEventListener('tutor:notification:read', (event) => {
                handleNotificationReadEvent(event && event.detail ? event.detail : null, { elements });
            });
            document._notificationReadListenerAttached = true;
        }

        // WebSocket connection
        let clientWs = null;
        if (options.autoConnectWs !== false) {
            clientWs = connectNotificationWebSocket({
                WebSocketClass: options.WebSocketClass,
                url: options.url,
                reconnectIntervals: options.reconnectIntervals,
                elements,
            });
        }

        return {
            elements,
            clientWs,
            disconnect: function () {
                if (clientWs) clientWs.disconnect();
            },
        };
    }

    // Auto-initialize when running in browser
    if (typeof document !== 'undefined' && typeof window !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => initNotifications());
        } else {
            initNotifications();
        }
    }

    return {
        RECONNECT_INTERVALS,
        getCsrfToken,
        getWebSocketUrl,
        getCurrentUnreadCount,
        updateUnreadCount,
        updateUnreadChatCount,
        prependNotificationItem,
        showNotificationToast,
        markNotificationItemReadInDom,
        handleNotificationReadEvent,
        markNotificationAsRead,
        markAllNotificationsAsRead,
        handleNotificationClick,
        connectNotificationWebSocket,
        initNotifications,
    };
}));
