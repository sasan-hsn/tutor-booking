const { test, describe, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert');

// Simple DOM Mock for Node environment
class MockClassList {
    constructor(element) {
        this.element = element;
        this.classes = new Set();
    }
    add(...names) {
        names.forEach(n => this.classes.add(n));
        this.element._className = Array.from(this.classes).join(' ');
    }
    remove(...names) {
        names.forEach(n => this.classes.delete(n));
        this.element._className = Array.from(this.classes).join(' ');
    }
    contains(name) {
        return this.classes.has(name);
    }
}

class MockElement {
    constructor(tagName) {
        this.tagName = tagName.toUpperCase();
        this._className = '';
        this.classList = new MockClassList(this);
        this.dataset = {};
        this.attributes = {};
        this.children = [];
        this.parentElement = null;
        this._innerHTML = '';
        this._textContent = '';
        this.eventListeners = {};
        this.disabled = false;
        this.style = {};
    }

    get firstChild() {
        return this.children[0] || null;
    }

    get lastChild() {
        return this.children[this.children.length - 1] || null;
    }

    get innerHTML() {
        if (this._innerHTML) return this._innerHTML;
        return this.children.map(c => c.innerHTML).join('');
    }
    set innerHTML(val) {
        this._innerHTML = val || '';
    }

    get className() {
        return this._className;
    }
    set className(val) {
        this._className = val || '';
        this.classList.classes = new Set(this._className.split(/\s+/).filter(Boolean));
    }

    get textContent() {
        return this._textContent || this.innerHTML.replace(/<[^>]*>/g, '').trim();
    }
    set textContent(val) {
        this._textContent = val;
        this._innerHTML = val;
    }

    setAttribute(k, v) {
        this.attributes[k] = v;
    }
    getAttribute(k) {
        return this.attributes[k] || null;
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    insertBefore(newNode, referenceNode) {
        newNode.parentElement = this;
        const index = this.children.indexOf(referenceNode);
        if (index !== -1) {
            this.children.splice(index, 0, newNode);
        } else {
            this.children.unshift(newNode);
        }
        return newNode;
    }

    remove() {
        if (this.parentElement) {
            const index = this.parentElement.children.indexOf(this);
            if (index !== -1) {
                this.parentElement.children.splice(index, 1);
            }
            this.parentElement = null;
        }
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
        const results = [];
        const sel = selector.trim();

        function match(el) {
            if (sel.startsWith('#')) {
                return el.attributes.id === sel.slice(1);
            }
            if (sel.includes('[data-id=')) {
                const matchId = sel.match(/\[data-id="?([^"\]]+)"?\]/);
                if (matchId && el.dataset.id === matchId[1]) return true;
            }
            if (sel.startsWith('.')) {
                return el.classList.contains(sel.slice(1));
            }
            if (el.tagName === sel.toUpperCase()) {
                return true;
            }
            return false;
        }

        function walk(node) {
            for (const child of node.children) {
                if (match(child)) results.push(child);
                walk(child);
            }
        }
        walk(this);
        return results;
    }

    closest(selector) {
        let cur = this;
        const sel = selector.trim();
        while (cur) {
            if (sel.startsWith('.') && cur.classList && cur.classList.contains(sel.slice(1))) {
                return cur;
            }
            if (sel.startsWith('#') && cur.attributes && cur.attributes.id === sel.slice(1)) {
                return cur;
            }
            cur = cur.parentElement;
        }
        return null;
    }

    addEventListener(event, handler) {
        if (!this.eventListeners[event]) this.eventListeners[event] = [];
        this.eventListeners[event].push(handler);
    }

    dispatchEvent(event) {
        const type = typeof event === 'string' ? event : event.type;
        const evtObj = typeof event === 'string' ? { type: event, target: this, preventDefault: () => {}, stopPropagation: () => {} } : event;
        try {
            if (!evtObj.target) {
                Object.defineProperty(evtObj, 'target', { value: this, configurable: true });
            }
        } catch (_) {}
        if (!evtObj.preventDefault) evtObj.preventDefault = () => {};
        if (!evtObj.stopPropagation) evtObj.stopPropagation = () => {};

        if (this.eventListeners[type]) {
            this.eventListeners[type].forEach(h => h(evtObj));
        }
    }
}

class MockDocument extends MockElement {
    constructor() {
        super('#document');
        this.cookie = 'csrftoken=mock-token-xyz;';
    }

    createElement(tag) {
        return new MockElement(tag);
    }

    getElementById(id) {
        return this.querySelector(`#${id}`);
    }
}

// Mock WebSocket
class MockWebSocket {
    constructor(url) {
        this.url = url;
        this.readyState = 0; // CONNECTING
        this.onopen = null;
        this.onmessage = null;
        this.onclose = null;
        this.onerror = null;
        MockWebSocket.instances.push(this);
    }

    simulateOpen() {
        this.readyState = 1; // OPEN
        if (this.onopen) this.onopen({});
    }

    simulateMessage(data) {
        if (this.onmessage) this.onmessage({ data: JSON.stringify(data) });
    }

    simulateClose(code = 1000) {
        this.readyState = 3; // CLOSED
        if (this.onclose) this.onclose({ code });
    }

    simulateError(error = {}) {
        if (this.onerror) this.onerror(error);
    }

    close() {
        this.readyState = 3;
        if (this.onclose) this.onclose({ code: 1000 });
    }
}
MockWebSocket.instances = [];

// Set up globals
global.document = new MockDocument();
global.window = {
    location: {
        protocol: 'http:',
        host: 'localhost:8000',
        href: 'http://localhost:8000/dashboard/student/',
    },
    WebSocket: MockWebSocket,
    bootstrap: {
        Toast: class {
            constructor(el, opts) {
                this.el = el;
                this.opts = opts;
            }
            show() {
                this.shown = true;
            }
        }
    }
};

let capturedFetches = [];
global.fetch = async (url, options) => {
    capturedFetches.push({ url, options });
    if (url.includes('/read/')) {
        return {
            ok: true,
            json: async () => ({ success: true, unread_count: 0 }),
        };
    }
    if (url.includes('/mark-all-read/')) {
        return {
            ok: true,
            json: async () => ({ success: true, unread_count: 0 }),
        };
    }
    return { ok: true, json: async () => ({}) };
};

const notifications = require('./notifications.js');

describe('Notifications Client Module', () => {
    let container, badge, badgeCount, dropdownCountBadge, markAllBtn, list, toastContainer;

    beforeEach(() => {
        global.document = new MockDocument();
        MockWebSocket.instances = [];
        capturedFetches = [];

        // Build DOM structure
        container = global.document.createElement('div');
        container.attributes.id = 'notificationDropdownContainer';

        const btn = global.document.createElement('button');
        btn.attributes.id = 'notificationDropdownBtn';

        badge = global.document.createElement('span');
        badge.attributes.id = 'notificationBadge';
        badge.classList.add('d-none');

        badgeCount = global.document.createElement('span');
        badgeCount.attributes.id = 'notificationBadgeCount';
        badgeCount.textContent = '0';
        badge.appendChild(badgeCount);
        btn.appendChild(badge);
        container.appendChild(btn);

        dropdownCountBadge = global.document.createElement('span');
        dropdownCountBadge.attributes.id = 'notificationDropdownCountBadge';
        dropdownCountBadge.textContent = '0 unread';
        container.appendChild(dropdownCountBadge);

        markAllBtn = global.document.createElement('button');
        markAllBtn.attributes.id = 'markAllReadBtn';
        markAllBtn.disabled = true;
        container.appendChild(markAllBtn);

        list = global.document.createElement('div');
        list.attributes.id = 'notificationList';
        container.appendChild(list);

        toastContainer = global.document.createElement('div');
        toastContainer.attributes.id = 'notificationToastContainer';

        global.document.appendChild(container);
        global.document.appendChild(toastContainer);
    });

    test('updateUnreadCount updates badge, count text, and toggles d-none', () => {
        notifications.updateUnreadCount(3, { badge, badgeCount, dropdownCountBadge, markAllBtn });
        assert.strictEqual(badgeCount.textContent, '3');
        assert.strictEqual(badge.classList.contains('d-none'), false);
        assert.strictEqual(dropdownCountBadge.textContent, '3 unread');
        assert.strictEqual(markAllBtn.disabled, false);

        notifications.updateUnreadCount(0, { badge, badgeCount, dropdownCountBadge, markAllBtn });
        assert.strictEqual(badgeCount.textContent, '0');
        assert.strictEqual(badge.classList.contains('d-none'), true);
        assert.strictEqual(dropdownCountBadge.textContent, '0 unread');
        assert.strictEqual(markAllBtn.disabled, true);
    });

    test('prependNotificationItem prepends new notification and maintains 20-item cap', () => {
        // Pre-populate with 20 items
        for (let i = 0; i < 20; i++) {
            const existing = global.document.createElement('div');
            existing.classList.add('notification-item');
            existing.dataset.id = `old-${i}`;
            list.appendChild(existing);
        }
        assert.strictEqual(list.querySelectorAll('.notification-item').length, 20);

        const newNotif = {
            id: 999,
            title: 'New Booking',
            message: 'A student requested a lesson',
            level: 'info',
            action_url: '/dashboard/teacher/',
            created_at: new Date().toISOString(),
        };

        const item = notifications.prependNotificationItem(newNotif, list);
        assert.notStrictEqual(item, null);
        assert.strictEqual(item.dataset.id, '999');
        assert.strictEqual(item.dataset.actionUrl, '/dashboard/teacher/');
        assert.strictEqual(item.classList.contains('notification-unread'), true);

        // List size stays capped at 20
        const items = list.querySelectorAll('.notification-item');
        assert.strictEqual(items.length, 20);
        // First item is the new one
        assert.strictEqual(items[0].dataset.id, '999');
        // The last item (old-19) was dropped
        assert.strictEqual(items[19].dataset.id, 'old-18');
    });

    test('prependNotificationItem removes empty state message if present', () => {
        const emptyMsg = global.document.createElement('div');
        emptyMsg.attributes.id = 'noNotificationsMessage';
        emptyMsg.textContent = 'No notifications yet';
        list.appendChild(emptyMsg);

        notifications.prependNotificationItem({
            id: 101,
            title: 'Title',
            message: 'Msg',
            level: 'success',
        }, list);

        assert.strictEqual(list.querySelector('#noNotificationsMessage'), null);
    });

    test('showNotificationToast renders toast in container with correct icon and content', () => {
        const notifData = {
            id: 42,
            title: 'Lesson Confirmed',
            message: 'Your lesson was confirmed!',
            level: 'success',
            action_url: '/dashboard/student/',
        };

        const toastEl = notifications.showNotificationToast(notifData, toastContainer);
        assert.notStrictEqual(toastEl, null);
        assert.strictEqual(toastContainer.children.includes(toastEl), true);
        assert.strictEqual(toastEl.innerHTML.includes('Lesson Confirmed'), true);
        assert.strictEqual(toastEl.innerHTML.includes('bi-check-circle-fill'), true);
    });

    test('WebSocket connection connects, receives message, updates badge, and shows toast', () => {
        const client = notifications.initNotifications({
            autoConnectWs: true,
            WebSocketClass: MockWebSocket,
        });

        assert.strictEqual(MockWebSocket.instances.length, 1);
        const ws = MockWebSocket.instances[0];
        ws.simulateOpen();

        // Simulate incoming notification
        ws.simulateMessage({
            type: 'notification_message',
            id: 55,
            title: 'Incoming Booking',
            message: 'New lesson request',
            level: 'info',
            action_url: '/dashboard/teacher/',
            unread_count: 1,
        });

        // Badge updated
        assert.strictEqual(badgeCount.textContent, '1');
        assert.strictEqual(badge.classList.contains('d-none'), false);

        // Item prepended to list
        const items = list.querySelectorAll('.notification-item');
        assert.strictEqual(items.length, 1);
        assert.strictEqual(items[0].dataset.id, '55');

        // Toast appended
        assert.strictEqual(toastContainer.children.length, 1);

        client.disconnect();
    });

    test('WebSocket message without explicit unread_count increments current badge count', () => {
        badgeCount.textContent = '2';
        badge.classList.remove('d-none');

        const client = notifications.initNotifications({
            autoConnectWs: true,
            WebSocketClass: MockWebSocket,
        });

        const ws = MockWebSocket.instances[0];
        ws.simulateOpen();

        ws.simulateMessage({
            type: 'notification_message',
            id: 56,
            title: 'Incoming Booking Without Count',
            message: 'New lesson request',
            level: 'info',
        });

        assert.strictEqual(badgeCount.textContent, '3');
        client.disconnect();
    });

    test('connectNotificationWebSocket dispatches tutor:notification CustomEvent on document with message payload', () => {
        let dispatchedEvent = null;
        global.document.addEventListener('tutor:notification', (e) => {
            dispatchedEvent = e;
        });

        const client = notifications.connectNotificationWebSocket({
            WebSocketClass: MockWebSocket,
        });

        const ws = MockWebSocket.instances[0];
        ws.simulateOpen();

        const payload = {
            type: 'notification_message',
            event_type: 'booking_requested',
            id: 88,
            title: 'New Booking',
            message: 'A student requested a lesson',
            action_url: '/dashboard/teacher/',
            level: 'info',
            unread_count: 5,
        };

        ws.simulateMessage(payload);

        assert.notStrictEqual(dispatchedEvent, null);
        assert.strictEqual(dispatchedEvent.type, 'tutor:notification');
        assert.deepStrictEqual(dispatchedEvent.detail, payload);

        client.disconnect();
    });

    test('connectNotificationWebSocket safely no-ops without error when document is undefined', () => {
        const originalDocument = global.document;
        delete global.document;
        try {
            const client = notifications.connectNotificationWebSocket({
                WebSocketClass: MockWebSocket,
            });

            const ws = MockWebSocket.instances[0];
            ws.simulateOpen();

            assert.doesNotThrow(() => {
                ws.simulateMessage({
                    type: 'notification_message',
                    event_type: 'booking_requested',
                    id: 89,
                    title: 'New Booking',
                    message: 'Test message',
                    level: 'info',
                });
            });

            client.disconnect();
        } finally {
            global.document = originalDocument;
        }
    });

    test('WebSocket reconnect uses backoff intervals: 1s, 2s, 5s, 10s', () => {
        const scheduledTimeouts = [];
        const originalSetTimeout = global.setTimeout;
        global.setTimeout = (fn, delay) => {
            scheduledTimeouts.push({ fn, delay });
            return scheduledTimeouts.length;
        };

        try {
            const client = notifications.initNotifications({
                autoConnectWs: true,
                WebSocketClass: MockWebSocket,
            });

            // 1st connection
            const ws1 = MockWebSocket.instances[0];
            ws1.simulateClose(); // closes without open -> attempt 1
            assert.strictEqual(scheduledTimeouts.length, 1);
            assert.strictEqual(scheduledTimeouts[0].delay, 1000);

            // Trigger reconnect attempt 1 -> ws2
            scheduledTimeouts[0].fn();
            assert.strictEqual(MockWebSocket.instances.length, 2);
            const ws2 = MockWebSocket.instances[1];
            ws2.simulateClose(); // attempt 2
            assert.strictEqual(scheduledTimeouts[1].delay, 2000);

            // Trigger reconnect attempt 2 -> ws3
            scheduledTimeouts[1].fn();
            const ws3 = MockWebSocket.instances[2];
            ws3.simulateClose(); // attempt 3
            assert.strictEqual(scheduledTimeouts[2].delay, 5000);

            // Trigger reconnect attempt 3 -> ws4
            scheduledTimeouts[2].fn();
            const ws4 = MockWebSocket.instances[3];
            ws4.simulateClose(); // attempt 4
            assert.strictEqual(scheduledTimeouts[3].delay, 10000);

            // Trigger reconnect attempt 4 -> ws5
            scheduledTimeouts[3].fn();
            const ws5 = MockWebSocket.instances[4];
            ws5.simulateClose(); // attempt 5 caps at 10000
            assert.strictEqual(scheduledTimeouts[4].delay, 10000);

            client.disconnect();
        } finally {
            global.setTimeout = originalSetTimeout;
        }
    });

    test('clicking notification marks as read and navigates to action_url', async () => {
        const client = notifications.initNotifications({ autoConnectWs: false });

        const item = global.document.createElement('div');
        item.classList.add('notification-item', 'notification-unread');
        item.dataset.id = '77';
        item.dataset.actionUrl = '/dashboard/lesson/77/';

        const dot = global.document.createElement('span');
        dot.classList.add('notification-dot');
        item.appendChild(dot);
        list.appendChild(item);

        badgeCount.textContent = '1';
        badge.classList.remove('d-none');

        let navigatedTo = null;
        const fakeWindow = {
            location: {
                set href(val) { navigatedTo = val; }
            }
        };

        await notifications.handleNotificationClick(item, {
            windowObj: fakeWindow,
            elements: { badge, badgeCount, dropdownCountBadge, markAllBtn }
        });

        // Verifies fetch was called with csrf token and keepalive
        assert.strictEqual(capturedFetches.length, 1);
        assert.strictEqual(capturedFetches[0].url, '/notifications/77/read/');
        assert.strictEqual(capturedFetches[0].options.headers['X-CSRFToken'], 'mock-token-xyz');
        assert.strictEqual(capturedFetches[0].options.keepalive, true);

        // Unread styling removed
        assert.strictEqual(item.classList.contains('notification-unread'), false);
        assert.strictEqual(item.querySelector('.notification-dot'), null);

        // Navigation occurred
        assert.strictEqual(navigatedTo, '/dashboard/lesson/77/');
    });

    test('clicking mark all read sends request and clears all unread items', async () => {
        const client = notifications.initNotifications({ autoConnectWs: false });

        for (let i = 1; i <= 3; i++) {
            const item = global.document.createElement('div');
            item.classList.add('notification-item', 'notification-unread');
            item.dataset.id = `${i}`;
            const dot = global.document.createElement('span');
            dot.classList.add('notification-dot');
            item.appendChild(dot);
            list.appendChild(item);
        }

        notifications.updateUnreadCount(3, { badge, badgeCount, dropdownCountBadge, markAllBtn });

        await notifications.markAllNotificationsAsRead({
            list,
            elements: { badge, badgeCount, dropdownCountBadge, markAllBtn }
        });

        // Verifies fetch
        assert.strictEqual(capturedFetches.length, 1);
        assert.strictEqual(capturedFetches[0].url, '/notifications/mark-all-read/');
        assert.strictEqual(capturedFetches[0].options.headers['X-CSRFToken'], 'mock-token-xyz');

        // All items unread state cleared
        const unreadItems = list.querySelectorAll('.notification-unread');
        assert.strictEqual(unreadItems.length, 0);
        const dots = list.querySelectorAll('.notification-dot');
        assert.strictEqual(dots.length, 0);

        // Badge reset to 0 and hidden
        assert.strictEqual(badgeCount.textContent, '0');
        assert.strictEqual(badge.classList.contains('d-none'), true);
        assert.strictEqual(markAllBtn.disabled, true);
    });

    test('markNotificationItemReadInDom strips notification-unread class and removes notification dot', () => {
        const item = global.document.createElement('div');
        item.classList.add('notification-item', 'notification-unread', 'bg-light-subtle');
        item.dataset.id = '101';
        const dot = global.document.createElement('span');
        dot.classList.add('notification-dot');
        item.appendChild(dot);
        list.appendChild(item);

        assert.strictEqual(item.classList.contains('notification-unread'), true);
        assert.notStrictEqual(item.querySelector('.notification-dot'), null);

        notifications.markNotificationItemReadInDom('101', list);

        assert.strictEqual(item.classList.contains('notification-unread'), false);
        assert.strictEqual(item.classList.contains('bg-light-subtle'), false);
        assert.strictEqual(item.querySelector('.notification-dot'), null);
    });

    test('WebSocket notification_read event updates badge count, marks item read in DOM, and dispatches tutor:notification:read', () => {
        badgeCount.textContent = '3';
        badge.classList.remove('d-none');

        const item1 = global.document.createElement('div');
        item1.classList.add('notification-item', 'notification-unread');
        item1.dataset.id = '42';
        const dot1 = global.document.createElement('span');
        dot1.classList.add('notification-dot');
        item1.appendChild(dot1);
        list.appendChild(item1);

        const item2 = global.document.createElement('div');
        item2.classList.add('notification-item', 'notification-unread');
        item2.dataset.id = '43';
        const dot2 = global.document.createElement('span');
        dot2.classList.add('notification-dot');
        item2.appendChild(dot2);
        list.appendChild(item2);

        let dispatchedReadEvent = null;
        global.document.addEventListener('tutor:notification:read', (e) => {
            dispatchedReadEvent = e;
        });

        const client = notifications.initNotifications({
            autoConnectWs: true,
            WebSocketClass: MockWebSocket,
        });

        const ws = MockWebSocket.instances[0];
        ws.simulateOpen();

        const readPayload = {
            type: 'notification_read',
            notification_ids: [42],
            unread_count: 2,
        };
        ws.simulateMessage(readPayload);

        // Badge updated
        assert.strictEqual(badgeCount.textContent, '2');
        assert.strictEqual(badge.classList.contains('d-none'), false);

        // item1 marked read, item2 remains unread
        assert.strictEqual(item1.classList.contains('notification-unread'), false);
        assert.strictEqual(item1.querySelector('.notification-dot'), null);
        assert.strictEqual(item2.classList.contains('notification-unread'), true);
        assert.notStrictEqual(item2.querySelector('.notification-dot'), null);

        // Event dispatched
        assert.notStrictEqual(dispatchedReadEvent, null);
        assert.strictEqual(dispatchedReadEvent.type, 'tutor:notification:read');
        assert.deepStrictEqual(dispatchedReadEvent.detail, readPayload);

        client.disconnect();
    });
});
