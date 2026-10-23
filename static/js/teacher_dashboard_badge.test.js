const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert');

// DOM Mock for Node.js test environment
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
        this.tagName = (tagName || 'DIV').toUpperCase();
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
    }

    get innerHTML() {
        if (this._innerHTML) return this._innerHTML;
        return this.children.map(c => c.innerHTML).join('');
    }
    set innerHTML(val) {
        this._innerHTML = val || '';
        this.children = [];
    }

    get className() {
        return this._className;
    }
    set className(val) {
        this._className = val || '';
        this.classList.classes = new Set(this._className.split(/\s+/).filter(Boolean));
    }

    get textContent() {
        return this._textContent !== undefined && this._textContent !== ''
            ? this._textContent
            : this.innerHTML.replace(/<[^>]*>/g, '').trim();
    }
    set textContent(val) {
        this._textContent = String(val);
        this._innerHTML = String(val);
    }

    setAttribute(k, v) {
        this.attributes[k] = v;
        if (k === 'id') this.id = v;
        if (k === 'class') this.className = v;
    }
    getAttribute(k) {
        if (k === 'id') return this.id || this.attributes['id'] || null;
        if (k === 'class') return this.className || this.attributes['class'] || null;
        return this.attributes[k] || null;
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
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
        const selectors = selector.split(',').map(s => s.trim());

        function match(el) {
            for (const sel of selectors) {
                if (sel.startsWith('#')) {
                    if ((el.id === sel.slice(1)) || (el.attributes.id === sel.slice(1))) return true;
                } else if (sel.startsWith('.')) {
                    if (el.classList.contains(sel.slice(1))) return true;
                } else if (el.tagName === sel.toUpperCase()) {
                    return true;
                }
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

        if (results.length === 0 && this._innerHTML) {
            for (const sel of selectors) {
                if (sel.startsWith('.')) {
                    const cls = sel.slice(1);
                    const regex = new RegExp(`class=["'][^"']*\\b${cls}\\b[^"']*["']`, 'g');
                    const matches = this._innerHTML.match(regex);
                    if (matches) {
                        for (let i = 0; i < matches.length; i++) {
                            const mockItem = new MockElement('div');
                            mockItem.classList.add(cls);
                            results.push(mockItem);
                        }
                    }
                }
            }
        }

        return results;
    }

    closest(selector) {
        let cur = this;
        const selectors = selector.split(',').map(s => s.trim());
        while (cur) {
            for (const sel of selectors) {
                if (sel.startsWith('.') && cur.classList && cur.classList.contains(sel.slice(1))) {
                    return cur;
                }
                if (sel.startsWith('#') && ((cur.id === sel.slice(1)) || (cur.attributes && cur.attributes.id === sel.slice(1)))) {
                    return cur;
                }
                if (cur.tagName === sel.toUpperCase()) {
                    return cur;
                }
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
        const evtObj = typeof event === 'string' ? { type: event, target: this } : event;
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
    }

    createElement(tag) {
        return new MockElement(tag);
    }

    getElementById(id) {
        return this.querySelector(`#${id}`);
    }
}

// Global setup prior to require('./main.js')
global.document = new MockDocument();
global.window = {
    location: { reload: () => {} },
    confirm: () => true,
    alert: () => {},
};

// Import code under test
const main = require('./main.js');
const {
    updateLessonRequestsBadge,
    getLessonRequestsBadgeCount,
    handleLessonRequestNotification,
    initLessonRequestsManager,
    resetProcessedNotificationIds,
} = main;

describe('Teacher Dashboard Lesson Requests Badge Controller', () => {
    let mockDoc;
    let badgeEl;
    let modalEl;
    let modalBodyEl;

    beforeEach(() => {
        if (typeof resetProcessedNotificationIds === 'function') {
            resetProcessedNotificationIds();
        }

        mockDoc = new MockDocument();
        global.document = mockDoc;
        global.window = {
            location: { reload: () => {} },
            confirm: () => true,
            alert: () => {},
        };

        // Badge element
        badgeEl = mockDoc.createElement('span');
        badgeEl.setAttribute('id', 'lessonRequestsBadge');
        badgeEl.className = 'badge-count d-none';
        badgeEl.textContent = '0';
        mockDoc.appendChild(badgeEl);

        // Modal element
        modalEl = mockDoc.createElement('div');
        modalEl.setAttribute('id', 'lessonRequestsModal');
        modalEl.dataset.url = '/booking/teacher/requests/';
        modalEl.dataset.respondUrl = '/booking/requests/0/respond/';
        mockDoc.appendChild(modalEl);

        // Modal body
        modalBodyEl = mockDoc.createElement('div');
        modalBodyEl.setAttribute('id', 'lessonRequestsModalBody');
        modalEl.appendChild(modalBodyEl);
    });

    describe('updateLessonRequestsBadge helper', () => {
        test('sets textContent and removes d-none when count > 0', () => {
            const count = updateLessonRequestsBadge(3, badgeEl);
            assert.strictEqual(count, 3);
            assert.strictEqual(badgeEl.textContent, '3');
            assert.strictEqual(badgeEl.classList.contains('d-none'), false);
        });

        test('sets textContent to 0 and adds d-none when count is 0', () => {
            badgeEl.className = 'badge-count';
            badgeEl.textContent = '2';
            const count = updateLessonRequestsBadge(0, badgeEl);
            assert.strictEqual(count, 0);
            assert.strictEqual(badgeEl.textContent, '0');
            assert.strictEqual(badgeEl.classList.contains('d-none'), true);
        });

        test('bounds negative counts at 0 with d-none applied', () => {
            badgeEl.className = 'badge-count';
            badgeEl.textContent = '1';
            const count = updateLessonRequestsBadge(-4, badgeEl);
            assert.strictEqual(count, 0);
            assert.strictEqual(badgeEl.textContent, '0');
            assert.strictEqual(badgeEl.classList.contains('d-none'), true);
        });

        test('handles invalid / non-integer input gracefully by defaulting to 0', () => {
            const count = updateLessonRequestsBadge('invalid', badgeEl);
            assert.strictEqual(count, 0);
            assert.strictEqual(badgeEl.textContent, '0');
            assert.strictEqual(badgeEl.classList.contains('d-none'), true);
        });
    });

    describe('Real-time event subscriptions (tutor:notification)', () => {
        beforeEach(() => {
            initLessonRequestsManager(mockDoc);
        });

        test('increments count and removes d-none on booking_requested', () => {
            badgeEl.textContent = '0';
            badgeEl.className = 'badge-count d-none';

            mockDoc.dispatchEvent({
                type: 'tutor:notification',
                detail: {
                    id: 101,
                    event_type: 'booking_requested',
                    title: 'New Lesson Request',
                },
            });

            assert.strictEqual(badgeEl.textContent, '1');
            assert.strictEqual(badgeEl.classList.contains('d-none'), false);
        });

        test('increments count and removes d-none on cancellation_requested', () => {
            badgeEl.textContent = '1';
            badgeEl.className = 'badge-count';

            mockDoc.dispatchEvent({
                type: 'tutor:notification',
                detail: {
                    id: 102,
                    event_type: 'cancellation_requested',
                    title: 'Cancellation Request',
                },
            });

            assert.strictEqual(badgeEl.textContent, '2');
            assert.strictEqual(badgeEl.classList.contains('d-none'), false);
        });

        test('decrements count on booking_expired', () => {
            badgeEl.textContent = '2';
            badgeEl.className = 'badge-count';

            mockDoc.dispatchEvent({
                type: 'tutor:notification',
                detail: {
                    id: 103,
                    event_type: 'booking_expired',
                    title: 'Booking Expired',
                },
            });

            assert.strictEqual(badgeEl.textContent, '1');
            assert.strictEqual(badgeEl.classList.contains('d-none'), false);
        });

        test('decrements count and adds d-none when booking_expired drops count to 0', () => {
            badgeEl.textContent = '1';
            badgeEl.className = 'badge-count';

            mockDoc.dispatchEvent({
                type: 'tutor:notification',
                detail: {
                    id: 104,
                    event_type: 'booking_expired',
                    title: 'Booking Expired',
                },
            });

            assert.strictEqual(badgeEl.textContent, '0');
            assert.strictEqual(badgeEl.classList.contains('d-none'), true);
        });

        test('bounds decrement at 0 when already 0 on booking_expired', () => {
            badgeEl.textContent = '0';
            badgeEl.className = 'badge-count d-none';

            mockDoc.dispatchEvent({
                type: 'tutor:notification',
                detail: {
                    id: 105,
                    event_type: 'booking_expired',
                    title: 'Booking Expired',
                },
            });

            assert.strictEqual(badgeEl.textContent, '0');
            assert.strictEqual(badgeEl.classList.contains('d-none'), true);
        });

        test('ignores non-request notification event types', () => {
            badgeEl.textContent = '3';
            badgeEl.className = 'badge-count';

            mockDoc.dispatchEvent({
                type: 'tutor:notification',
                detail: {
                    id: 106,
                    event_type: 'booking_confirmed',
                    title: 'Booking Confirmed',
                },
            });

            assert.strictEqual(badgeEl.textContent, '3');
        });

        test('idempotency guard ignores duplicate notifications with identical IDs', () => {
            badgeEl.textContent = '1';
            badgeEl.className = 'badge-count';

            const payload = {
                type: 'tutor:notification',
                detail: {
                    id: 200,
                    event_type: 'booking_requested',
                    title: 'New Lesson Request',
                },
            };

            // First dispatch: increments to 2
            mockDoc.dispatchEvent(payload);
            assert.strictEqual(badgeEl.textContent, '2');

            // Second dispatch with same id: ignored
            mockDoc.dispatchEvent(payload);
            assert.strictEqual(badgeEl.textContent, '2');

            // Third dispatch with different id: increments to 3
            mockDoc.dispatchEvent({
                type: 'tutor:notification',
                detail: {
                    id: 201,
                    event_type: 'booking_requested',
                    title: 'New Lesson Request 2',
                },
            });
            assert.strictEqual(badgeEl.textContent, '3');
        });

        test('active modal safeguard: modifies badge without re-rendering or replacing active modal body while modal is open', () => {
            // Set modal as actively open (.show)
            modalEl.classList.add('show');
            modalBodyEl.innerHTML = '<div class="request-item" data-booking-id="10">Active Review Content</div>';
            const initialHtml = modalBodyEl.innerHTML;

            badgeEl.textContent = '1';

            // Background notification arrives
            mockDoc.dispatchEvent({
                type: 'tutor:notification',
                detail: {
                    id: 300,
                    event_type: 'booking_requested',
                    title: 'Another request',
                },
            });

            // Badge updated
            assert.strictEqual(badgeEl.textContent, '2');
            // Modal body preserved intact without re-render
            assert.strictEqual(modalBodyEl.innerHTML, initialHtml);
            assert.strictEqual(modalBodyEl.querySelectorAll('.request-item').length, 1);
        });
    });

    describe('Modal Reconciliation and Action Handling', () => {
        beforeEach(() => {
            initLessonRequestsManager(mockDoc);
        });

        test('reconciles badge count from fetched modal body items on modal show', async () => {
            badgeEl.textContent = '5'; // Stale count before fetch

            // Mock fetch returning 2 request items
            global.fetch = async () => ({
                text: async () => `
                    <div class="request-list">
                        <div class="request-item" data-booking-id="1">Request 1</div>
                        <div class="request-item" data-booking-id="2">Request 2</div>
                    </div>
                `,
            });

            modalEl.dispatchEvent('show.bs.modal');
            // Allow microtasks/promises to drain
            await new Promise(resolve => setTimeout(resolve, 0));

            // Should have reconciled to 2
            assert.strictEqual(badgeEl.textContent, '2');
            assert.strictEqual(badgeEl.classList.contains('d-none'), false);
        });

        test('reconciles badge count to 0 and adds d-none when fetched modal has 0 items', async () => {
            badgeEl.textContent = '3';
            badgeEl.className = 'badge-count';

            global.fetch = async () => ({
                text: async () => `
                    <div class="empty-state">
                        <p>No pending lesson requests.</p>
                    </div>
                `,
            });

            modalEl.dispatchEvent('show.bs.modal');
            await new Promise(resolve => setTimeout(resolve, 0));

            assert.strictEqual(badgeEl.textContent, '0');
            assert.strictEqual(badgeEl.classList.contains('d-none'), true);
        });

        test('decrements count on modal item accept/decline action without removing badge element', async () => {
            badgeEl.textContent = '2';
            badgeEl.className = 'badge-count';

            // Setup request item with accept button in modalBody
            const requestItem = mockDoc.createElement('div');
            requestItem.className = 'request-item';
            requestItem.dataset.bookingId = '42';

            const acceptBtn = mockDoc.createElement('button');
            acceptBtn.className = 'btn-accept';
            acceptBtn.dataset.bookingId = '42';
            requestItem.appendChild(acceptBtn);
            modalBodyEl.appendChild(requestItem);

            // Mock fetch for accept action
            global.fetch = async () => ({
                ok: true,
                status: 200,
            });

            // Click accept button
            modalBodyEl.dispatchEvent({
                type: 'click',
                target: acceptBtn,
            });
            await new Promise(resolve => setTimeout(resolve, 0));

            // Badge element must still exist in DOM and count decremented to 1
            assert.strictEqual(mockDoc.getElementById('lessonRequestsBadge'), badgeEl);
            assert.strictEqual(badgeEl.textContent, '1');
            assert.strictEqual(badgeEl.classList.contains('d-none'), false);
        });

        test('decrements count to 0, applies d-none, and preserves badge element when last item is accepted', async () => {
            badgeEl.textContent = '1';
            badgeEl.className = 'badge-count';

            const requestItem = mockDoc.createElement('div');
            requestItem.className = 'request-item';
            requestItem.dataset.bookingId = '43';

            const declineBtn = mockDoc.createElement('button');
            declineBtn.className = 'btn-decline';
            declineBtn.dataset.bookingId = '43';
            requestItem.appendChild(declineBtn);
            modalBodyEl.appendChild(requestItem);

            global.fetch = async () => ({
                ok: true,
                status: 200,
            });

            modalBodyEl.dispatchEvent({
                type: 'click',
                target: declineBtn,
            });
            await new Promise(resolve => setTimeout(resolve, 0));

            // Badge must NOT be removed from DOM, should show 0 with d-none
            assert.strictEqual(mockDoc.getElementById('lessonRequestsBadge'), badgeEl);
            assert.strictEqual(badgeEl.textContent, '0');
            assert.strictEqual(badgeEl.classList.contains('d-none'), true);
        });
    });
});
