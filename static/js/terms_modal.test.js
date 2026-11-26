const { test, describe, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert');

const mainModule = require('./main.js');

class MockClassList {
    constructor(el) {
        this.el = el;
        this.classes = new Set();
    }
    add(...names) {
        names.forEach(n => this.classes.add(n));
    }
    remove(...names) {
        names.forEach(n => this.classes.delete(n));
    }
    contains(name) {
        return this.classes.has(name);
    }
}

class MockEl {
    constructor(tagName = 'DIV', id = '') {
        this.tagName = tagName.toUpperCase();
        this.id = id;
        this.dataset = {};
        this.disabled = false;
        this.checked = false;
        this.textContent = '';
        this.innerHTML = '';
        this.classList = new MockClassList(this);
        this.listeners = {};
    }
    addEventListener(type, fn) {
        if (!this.listeners[type]) this.listeners[type] = [];
        this.listeners[type].push(fn);
    }
    async dispatchEvent(event) {
        const handlers = this.listeners[event.type] || [];
        for (const fn of handlers) {
            await fn(event);
        }
    }
    closest() {
        return null;
    }
}

describe('Terms Acceptance Modal & Action Resumption', () => {
    let originalDocument;
    let originalWindow;
    let originalFetch;
    let originalBootstrap;
    let elements;
    let modalStates;
    let fetchCalls;
    let reloaded;

    beforeEach(() => {
        originalDocument = global.document;
        originalWindow = global.window;
        originalFetch = global.fetch;
        originalBootstrap = global.bootstrap;

        modalStates = {
            termsAcceptanceModal: { shown: 0, hidden: 0 },
            confirmBookingModal: { shown: 0, hidden: 0 },
        };
        fetchCalls = [];
        reloaded = false;

        elements = {
            termsAcceptanceModal: new MockEl('DIV', 'termsAcceptanceModal'),
            acceptTermsCheckbox: new MockEl('INPUT', 'acceptTermsCheckbox'),
            acceptTermsContinueBtn: new MockEl('BUTTON', 'acceptTermsContinueBtn'),
            termsAcceptanceErrorAlert: new MockEl('DIV', 'termsAcceptanceErrorAlert'),
            dayPicker: new MockEl('DIV', 'dayPicker'),
            confirmBookingModal: new MockEl('DIV', 'confirmBookingModal'),
            confirmBookingBtn: new MockEl('BUTTON', 'confirmBookingBtn'),
            modalVerificationAlert: new MockEl('DIV', 'modalVerificationAlert'),
        };

        elements.termsAcceptanceModal.dataset.acceptUrl = '/accounts/terms/accept/';
        elements.termsAcceptanceModal.dataset.termsVersion = '2026.1';
        elements.acceptTermsContinueBtn.disabled = true;
        elements.termsAcceptanceErrorAlert.classList.add('d-none');

        elements.dayPicker.dataset.bookSlotUrl = '/dashboard/student/book-slot/';
        elements.dayPicker.dataset.termsRequired = 'true';
        elements.dayPicker.dataset.termsAccepted = 'false';

        elements.confirmBookingBtn.dataset.startAt = '2026-12-01T10:00:00+00:00';
        elements.confirmBookingBtn.dataset.lessonOption = 'regular_50';

        const docListeners = {};
        global.document = {
            cookie: 'csrftoken=test-csrf-123',
            getElementById: (id) => elements[id] || null,
            querySelectorAll: (selector) => {
                if (selector === '[data-terms-accepted]') {
                    return [elements.dayPicker];
                }
                return [];
            },
            addEventListener: (type, fn) => {
                if (!docListeners[type]) docListeners[type] = [];
                docListeners[type].push(fn);
            },
        };

        class MockModal {
            constructor(el) {
                this.el = el;
            }
            show() {
                modalStates[this.el.id].shown += 1;
            }
            hide() {
                modalStates[this.el.id].hidden += 1;
            }
            static getOrCreateInstance(el) {
                return new MockModal(el);
            }
        }

        global.bootstrap = { Modal: MockModal };
        global.window = {
            bootstrap: global.bootstrap,
            location: {
                reload: () => { reloaded = true; },
            },
        };
    });

    afterEach(() => {
        global.document = originalDocument;
        global.window = originalWindow;
        global.fetch = originalFetch;
        global.bootstrap = originalBootstrap;
    });

    test('checkbox controls disabled state of Accept & Continue button', async () => {
        assert.strictEqual(typeof mainModule.initTermsAcceptanceModal, 'function');
        mainModule.initTermsAcceptanceModal();

        assert.strictEqual(elements.acceptTermsContinueBtn.disabled, true);

        elements.acceptTermsCheckbox.checked = true;
        await elements.acceptTermsCheckbox.dispatchEvent({ type: 'change' });
        assert.strictEqual(elements.acceptTermsContinueBtn.disabled, false);

        elements.acceptTermsCheckbox.checked = false;
        await elements.acceptTermsCheckbox.dispatchEvent({ type: 'change' });
        assert.strictEqual(elements.acceptTermsContinueBtn.disabled, true);
    });

    test('booking confirmation gates on unaccepted terms, submits acceptance without reload, and resumes booking', async () => {
        global.fetch = async (url, options) => {
            fetchCalls.push({ url, options });
            if (url === '/accounts/terms/accept/') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ accepted: true, version: '2026.1' }),
                };
            }
            if (url === '/dashboard/student/book-slot/') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ success: true, booking_id: 42 }),
                };
            }
            throw new Error(`Unexpected URL: ${url}`);
        };

        mainModule.initTermsAcceptanceModal();
        mainModule.initBookingModal();

        // Click Confirm Booking while terms are not accepted
        await elements.confirmBookingBtn.dispatchEvent({ type: 'click' });

        assert.strictEqual(fetchCalls.length, 0);
        assert.strictEqual(modalStates.termsAcceptanceModal.shown, 1);
        assert.strictEqual(reloaded, false);

        // Check mandatory checkbox and click Accept & Continue
        elements.acceptTermsCheckbox.checked = true;
        await elements.acceptTermsCheckbox.dispatchEvent({ type: 'change' });
        await elements.acceptTermsContinueBtn.dispatchEvent({ type: 'click' });

        assert.strictEqual(fetchCalls.length, 2);
        assert.strictEqual(fetchCalls[0].url, '/accounts/terms/accept/');
        assert.strictEqual(fetchCalls[0].options.headers['X-CSRFToken'], 'test-csrf-123');
        assert.strictEqual(fetchCalls[1].url, '/dashboard/student/book-slot/');
        assert.strictEqual(elements.dayPicker.dataset.termsAccepted, 'true');
        assert.strictEqual(modalStates.termsAcceptanceModal.hidden, 1);
        assert.strictEqual(reloaded, true);
    });

    test('failed acceptance request displays inline error without closing modal or running callback', async () => {
        let resumed = false;
        global.fetch = async (url) => {
            fetchCalls.push({ url });
            return {
                ok: false,
                status: 400,
                json: async () => ({ error: 'You must accept the Terms of Service.' }),
            };
        };

        mainModule.initTermsAcceptanceModal();
        mainModule.openTermsAcceptanceModal(() => {
            resumed = true;
        });

        elements.acceptTermsCheckbox.checked = true;
        await elements.acceptTermsCheckbox.dispatchEvent({ type: 'change' });
        await elements.acceptTermsContinueBtn.dispatchEvent({ type: 'click' });

        assert.strictEqual(resumed, false);
        assert.strictEqual(modalStates.termsAcceptanceModal.hidden, 0);
        assert.strictEqual(elements.termsAcceptanceErrorAlert.classList.contains('d-none'), false);
        assert.strictEqual(
            elements.termsAcceptanceErrorAlert.textContent,
            'You must accept the Terms of Service.'
        );
    });

    test('403 terms_not_accepted for current rendered version triggers terms modal and resumes booking on accept', async () => {
        elements.dayPicker.dataset.termsAccepted = 'true';
        let bookCalls = 0;

        global.fetch = async (url) => {
            fetchCalls.push({ url });
            if (url === '/dashboard/student/book-slot/') {
                bookCalls += 1;
                if (bookCalls === 1) {
                    return {
                        ok: false,
                        status: 403,
                        json: async () => ({ error: 'terms_not_accepted', terms_version: '2026.1' }),
                    };
                }
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ success: true, booking_id: 99 }),
                };
            }
            if (url === '/accounts/terms/accept/') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ accepted: true, version: '2026.1' }),
                };
            }
            throw new Error(`Unexpected URL: ${url}`);
        };

        mainModule.initTermsAcceptanceModal();
        mainModule.initBookingModal();

        await elements.confirmBookingBtn.dispatchEvent({ type: 'click' });
        assert.strictEqual(bookCalls, 1);
        assert.strictEqual(elements.dayPicker.dataset.termsAccepted, 'false');
        assert.strictEqual(modalStates.termsAcceptanceModal.shown, 1);

        elements.acceptTermsCheckbox.checked = true;
        await elements.acceptTermsCheckbox.dispatchEvent({ type: 'change' });
        await elements.acceptTermsContinueBtn.dispatchEvent({ type: 'click' });

        assert.strictEqual(bookCalls, 2);
        assert.strictEqual(elements.dayPicker.dataset.termsAccepted, 'true');
        assert.strictEqual(reloaded, true);
    });

    test('403 terms_not_accepted with bumped version reloads page so student views fresh terms', async () => {
        elements.dayPicker.dataset.termsAccepted = 'true';

        global.fetch = async (url) => {
            fetchCalls.push({ url });
            return {
                ok: false,
                status: 403,
                json: async () => ({ error: 'terms_not_accepted', terms_version: '2026.2' }),
            };
        };

        mainModule.initTermsAcceptanceModal();
        mainModule.initBookingModal();

        await elements.confirmBookingBtn.dispatchEvent({ type: 'click' });
        assert.strictEqual(reloaded, true);
        assert.strictEqual(modalStates.termsAcceptanceModal.shown, 0);
    });
});
