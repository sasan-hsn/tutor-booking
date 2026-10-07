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
        this.innerHTML = '';
        this._textContent = '';
        this.eventListeners = {};
        this.onclick = null;
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
        this.innerHTML = val;
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

    replaceWith(newElement) {
        if (!this.parentElement) return;
        const index = this.parentElement.children.indexOf(this);
        if (index !== -1) {
            newElement.parentElement = this.parentElement;
            this.parentElement.children.splice(index, 1, newElement);
        }
        this.parentElement = null;
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
                if (sel.startsWith('.')) {
                    const cls = sel.slice(1);
                    if (el.classList.contains(cls)) return true;
                } else if (sel.startsWith('a.') || sel.startsWith('span.')) {
                    const [tag, cls] = sel.split('.');
                    if (el.tagName === tag.toUpperCase() && el.classList.contains(cls)) return true;
                } else if (sel.toUpperCase() === el.tagName) {
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
        return results;
    }

    addEventListener(event, handler) {
        if (!this.eventListeners[event]) this.eventListeners[event] = [];
        this.eventListeners[event].push(handler);
    }

    dispatchEvent(event) {
        if (this.onclick) this.onclick(event);
        if (this.eventListeners[event.type]) {
            this.eventListeners[event.type].forEach(h => h(event));
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

    querySelectorAll(selector) {
        // Handle attribute selectors like .lesson-card[data-start-at]
        if (selector.includes('.lesson-card')) {
            const cards = [];
            function walk(node) {
                for (const child of node.children) {
                    if (child.classList.contains('lesson-card')) {
                        if (selector.includes('[data-start-at]')) {
                            if (child.dataset.startAt && child.dataset.endAt) {
                                cards.push(child);
                            }
                        } else {
                            cards.push(child);
                        }
                    }
                    walk(child);
                }
            }
            walk(this);
            return cards;
        }
        return super.querySelectorAll(selector);
    }
}

// Global setup
global.document = new MockDocument();
global.window = {
    location: { pathname: '/teacher/' }
};
global.setInterval = (fn, ms) => {
    return 123;
};
global.clearInterval = (id) => {};

// Import main.js
const { initLessonJoinWindowWatcher, updateLessonJoinWindows } = require('./main.js');

describe('Lesson Join Window Watcher', () => {
    let card, cardBody, teacherEl, badgeEl;
    const startAt = new Date('2026-10-06T14:00:00Z');
    const endAt = new Date('2026-10-06T14:50:00Z');

    beforeEach(() => {
        global.document = new MockDocument();
        card = global.document.createElement('div');
        card.classList.add('lesson-card');
        card.dataset.startAt = startAt.toISOString();
        card.dataset.endAt = endAt.toISOString();
        card.dataset.status = 'confirmed';
        card.dataset.isTeacher = 'true';
        card.dataset.meetingLink = 'https://meet.google.com/test-room';
        card.dataset.settingsUrl = '/teacher/settings/booking/?next=/teacher/';
        card.dataset.lessonType = 'Regular Lesson';

        cardBody = global.document.createElement('div');
        cardBody.classList.add('lesson-card-body');

        teacherEl = global.document.createElement('span');
        teacherEl.classList.add('lesson-card-teacher');
        teacherEl.textContent = 'Alice Student';
        cardBody.appendChild(teacherEl);

        badgeEl = global.document.createElement('span');
        badgeEl.classList.add('badge-lesson-type');
        badgeEl.textContent = 'Regular Lesson';
        cardBody.appendChild(badgeEl);

        card.appendChild(cardBody);
        global.document.appendChild(card);
    });

    test('outside join window (15m before start): card has no join button or joinable styling', () => {
        const now = new Date('2026-10-06T13:45:00Z'); // 15m before
        updateLessonJoinWindows(now);

        assert.strictEqual(card.classList.contains('lesson-card-joinable'), false);
        const joinBtn = cardBody.querySelector('.btn-join-lesson');
        assert.strictEqual(joinBtn, null);
        assert.strictEqual(cardBody.querySelector('.badge-live'), null);
    });

    test('enters join window (10m before start): dynamically renders Join Lesson button and joinable class', () => {
        const now = new Date('2026-10-06T13:50:00Z'); // 10m before
        updateLessonJoinWindows(now);

        assert.strictEqual(card.classList.contains('lesson-card-joinable'), true);
        const joinBtn = cardBody.querySelector('.btn-join-lesson');
        assert.notStrictEqual(joinBtn, null);
        assert.strictEqual(joinBtn.getAttribute('href') || joinBtn.href, 'https://meet.google.com/test-room');
        assert.strictEqual(joinBtn.target, '_blank');
        assert.strictEqual(joinBtn.rel, 'noopener noreferrer');
        assert.strictEqual(cardBody.querySelector('.badge-live'), null);
    });

    test('teacher without meeting link: renders Set Meeting Link button when entering join window', () => {
        card.dataset.meetingLink = '';
        const now = new Date('2026-10-06T13:52:00Z'); // 8m before
        updateLessonJoinWindows(now);

        assert.strictEqual(card.classList.contains('lesson-card-joinable'), true);
        const setLinkBtn = cardBody.querySelector('.btn-set-meeting-link');
        assert.notStrictEqual(setLinkBtn, null);
        assert.ok(setLinkBtn.textContent.includes('Set Meeting Link'));
        assert.strictEqual(setLinkBtn.getAttribute('href') || setLinkBtn.href, '/teacher/settings/booking/?next=/teacher/');
        assert.strictEqual(cardBody.querySelector('.btn-join-lesson'), null);
    });

    test('student without meeting link: keeps card clean without broken buttons', () => {
        card.dataset.isTeacher = 'false';
        card.dataset.meetingLink = '';
        const now = new Date('2026-10-06T13:55:00Z'); // 5m before
        updateLessonJoinWindows(now);

        assert.strictEqual(card.classList.contains('lesson-card-joinable'), true);
        assert.strictEqual(cardBody.querySelector('.btn-join-lesson'), null);
        assert.strictEqual(cardBody.querySelector('.btn-set-meeting-link'), null);
    });

    test('start_at arrives: dynamically transitions badge to Live Now indicator', () => {
        const now = new Date('2026-10-06T14:00:00Z'); // exactly at start
        updateLessonJoinWindows(now);

        const liveBadge = cardBody.querySelector('.badge-live');
        assert.notStrictEqual(liveBadge, null);
        assert.ok(liveBadge.innerHTML.includes('badge-live-dot'));
        assert.ok(liveBadge.textContent.includes('Live Now'));
        assert.strictEqual(cardBody.querySelector('.badge-lesson-type'), null);
        assert.notStrictEqual(cardBody.querySelector('.btn-join-lesson'), null);
    });

    test('mid lesson: badge remains Live Now and join button remains', () => {
        const now = new Date('2026-10-06T14:25:00Z'); // 25m in
        updateLessonJoinWindows(now);

        assert.notStrictEqual(cardBody.querySelector('.badge-live'), null);
        assert.notStrictEqual(cardBody.querySelector('.btn-join-lesson'), null);
    });

    test('end_at passes (within 10m grace period): teacher transitions to Needs Action but retains Join button', () => {
        const now = new Date('2026-10-06T14:55:00Z'); // 5m after end_at
        updateLessonJoinWindows(now);

        assert.strictEqual(cardBody.querySelector('.badge-live'), null);
        const needsActionBadge = cardBody.querySelector('.badge-needs-action');
        assert.notStrictEqual(needsActionBadge, null);
        assert.ok(needsActionBadge.textContent.includes('Needs Action'));
        assert.strictEqual(card.classList.contains('lesson-card-needs-action'), true);
        assert.notStrictEqual(cardBody.querySelector('.btn-join-lesson'), null);
    });

    test('end_at + 10m passes: removes join button and cleans up joinable styling', () => {
        const now = new Date('2026-10-06T15:00:01Z'); // 10m 1s after end_at
        updateLessonJoinWindows(now);

        assert.strictEqual(cardBody.querySelector('.btn-join-lesson'), null);
        assert.strictEqual(cardBody.querySelector('.btn-set-meeting-link'), null);
        assert.strictEqual(card.classList.contains('lesson-card-joinable'), false);
        assert.strictEqual(card.classList.contains('lesson-card-needs-action'), true);
    });

    test('dynamically inserted Join Lesson button stops event propagation on click', () => {
        const now = new Date('2026-10-06T13:55:00Z');
        updateLessonJoinWindows(now);

        const joinBtn = cardBody.querySelector('.btn-join-lesson');
        assert.notStrictEqual(joinBtn, null);

        let propagationStopped = false;
        const fakeEvent = {
            type: 'click',
            stopPropagation: () => {
                propagationStopped = true;
            }
        };
        joinBtn.dispatchEvent(fakeEvent);
        assert.strictEqual(propagationStopped, true);
    });

    test('non-confirmed booking: never marked joinable', () => {
        card.dataset.status = 'cancelled';
        const now = new Date('2026-10-06T14:05:00Z');
        updateLessonJoinWindows(now);

        assert.strictEqual(card.classList.contains('lesson-card-joinable'), false);
        assert.strictEqual(cardBody.querySelector('.btn-join-lesson'), null);
        assert.strictEqual(cardBody.querySelector('.badge-live'), null);
    });

    test('watcher initialization does not leak or error when no cards exist', () => {
        global.document = new MockDocument();
        assert.doesNotThrow(() => {
            const interval = initLessonJoinWindowWatcher();
            assert.strictEqual(interval, null);
        });
    });
});
