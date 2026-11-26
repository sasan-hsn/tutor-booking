const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert');

const {
    generateIdempotencyToken,
    prefillManualTopupForm,
    resetManualTopupForm,
    formatDisputeCountdown,
    initTeacherFinance,
} = require('./teacher_finance.js');

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
    constructor(tag = 'DIV', id = '') {
        this.tagName = tag.toUpperCase();
        this.id = id;
        this.value = '';
        this.innerHTML = '';
        this.textContent = '';
        this.disabled = false;
        this.dataset = {};
        this.classList = new MockClassList(this);
        this.listeners = {};
        this.children = [];
    }
    addEventListener(type, fn) {
        if (!this.listeners[type]) this.listeners[type] = [];
        this.listeners[type].push(fn);
    }
    dispatchEvent(event) {
        const handlers = this.listeners[event.type] || [];
        for (const h of handlers) {
            h(event);
        }
    }
    appendChild(child) {
        this.children.push(child);
        return child;
    }
    remove() {}
    setAttribute() {}
    closest() {
        return null;
    }
    querySelector() {
        return null;
    }
}

describe('Teacher Finance JS Controller', () => {
    let elements;
    let mockDoc;
    let modalShown;
    let modalHidden;

    beforeEach(() => {
        modalShown = false;
        modalHidden = false;

        elements = {
            openManualTopupBtn: new MockEl('BUTTON', 'openManualTopupBtn'),
            manualTopupModal: new MockEl('DIV', 'manualTopupModal'),
            manualTopupForm: new MockEl('FORM', 'manualTopupForm'),
            manualTopupStudent: new MockEl('SELECT', 'manualTopupStudent'),
            manualTopupAmount: new MockEl('INPUT', 'manualTopupAmount'),
            manualTopupReference: new MockEl('INPUT', 'manualTopupReference'),
            manualTopupNotes: new MockEl('TEXTAREA', 'manualTopupNotes'),
            manualTopupIdempotencyKey: new MockEl('INPUT', 'manualTopupIdempotencyKey'),
            manualTopupErrorAlert: new MockEl('DIV', 'manualTopupErrorAlert'),
            manualTopupSubmitBtn: new MockEl('BUTTON', 'manualTopupSubmitBtn'),
            financeOverviewContainer: new MockEl('SECTION', 'financeOverviewContainer'),
            financeClaimsContainer: new MockEl('DIV', 'financeClaimsContainer'),
            financeLedgerContainer: new MockEl('DIV', 'financeLedgerContainer'),
            notificationToastContainer: new MockEl('DIV', 'notificationToastContainer'),
        };
        elements.manualTopupForm.action = '/dashboard/finance/manual-topup/';
        elements.manualTopupErrorAlert.classList.add('d-none');

        mockDoc = {
            cookie: 'csrftoken=test-csrf-token',
            listeners: {},
            getElementById: (id) => elements[id] || null,
            createElement: (tag) => new MockEl(tag),
            addEventListener: function (type, fn) {
                if (!this.listeners[type]) this.listeners[type] = [];
                this.listeners[type].push(fn);
            },
            dispatchEvent: function (event) {
                const handlers = this.listeners[event.type] || [];
                for (const h of handlers) {
                    h(event);
                }
            },
        };

        global.window = {
            bootstrap: {
                Modal: {
                    getOrCreateInstance: () => ({
                        show: () => { modalShown = true; },
                        hide: () => { modalHidden = true; },
                    }),
                },
                Toast: function () {
                    return { show: () => {} };
                },
            },
        };
    });

    test('generateIdempotencyToken returns valid UUID v4 format', () => {
        const token1 = generateIdempotencyToken();
        const token2 = generateIdempotencyToken();
        assert.match(
            token1,
            /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
        );
        assert.notStrictEqual(token1, token2);
    });

    test('clicking Credit Deposit claim button pre-fills student ID, amount, reference, notes, and UUID', () => {
        initTeacherFinance(mockDoc);

        const claimBtn = new MockEl('BUTTON');
        claimBtn.dataset = {
            studentId: '17',
            amount: '45.00',
            reference: '0xclaimedtxhash999',
            notes: 'Top-up claim for Order #42',
        };

        elements.financeClaimsContainer.dispatchEvent({
            type: 'click',
            target: {
                closest: (sel) => (sel === '.btn-credit-deposit' ? claimBtn : null),
            },
        });

        assert.strictEqual(elements.manualTopupStudent.value, '17');
        assert.strictEqual(elements.manualTopupAmount.value, '45.00');
        assert.strictEqual(elements.manualTopupReference.value, '0xclaimedtxhash999');
        assert.strictEqual(elements.manualTopupNotes.value, 'Top-up claim for Order #42');
        assert.ok(elements.manualTopupIdempotencyKey.value.length >= 32);
        assert.strictEqual(modalShown, true);
    });

    test('resetManualTopupForm clears fields and sets a fresh UUID', () => {
        elements.manualTopupStudent.value = '17';
        elements.manualTopupAmount.value = '45.00';
        elements.manualTopupReference.value = 'OLD_REF';
        elements.manualTopupNotes.value = 'Old note';
        elements.manualTopupErrorAlert.classList.remove('d-none');

        resetManualTopupForm(elements);

        assert.strictEqual(elements.manualTopupStudent.value, '');
        assert.strictEqual(elements.manualTopupAmount.value, '');
        assert.strictEqual(elements.manualTopupReference.value, '');
        assert.strictEqual(elements.manualTopupNotes.value, '');
        assert.ok(elements.manualTopupIdempotencyKey.value.length >= 32);
        assert.strictEqual(elements.manualTopupErrorAlert.classList.contains('d-none'), true);
    });

    test('submitting form updates overview, claims, and ledger without reload and shows toast', async () => {
        let fetchedUrl = null;
        let fetchedOptions = null;

        global.FormData = function (form) {
            return { _form: form };
        };
        global.fetch = async (url, options) => {
            fetchedUrl = url;
            fetchedOptions = options;
            return {
                ok: true,
                json: async () => ({
                    success: true,
                    message: 'Credited $45.00 USD to Alice.',
                    overview_html: '<div id="updatedOverview">$45.00</div>',
                    claims_html: '<div id="updatedClaims">Empty</div>',
                    ledger_html: '<div id="updatedLedger">CREDIT $45.00</div>',
                }),
            };
        };

        initTeacherFinance(mockDoc);
        prefillManualTopupForm(
            {
                orderId: '42',
                studentId: '17',
                amount: '45.00',
                reference: '0xabc',
                notes: 'Claim #42',
            },
            elements
        );

        let prevented = false;
        const submitHandler = elements.manualTopupForm.listeners.submit[0];
        await submitHandler({
            preventDefault: () => { prevented = true; },
        });

        assert.strictEqual(prevented, true);
        assert.strictEqual(fetchedUrl, '/dashboard/finance/manual-topup/');
        assert.strictEqual(fetchedOptions.method, 'POST');
        assert.strictEqual(fetchedOptions.headers['X-CSRFToken'], 'test-csrf-token');
        assert.strictEqual(
            elements.financeOverviewContainer.innerHTML,
            '<div id="updatedOverview">$45.00</div>'
        );
        assert.strictEqual(
            elements.financeClaimsContainer.innerHTML,
            '<div id="updatedClaims">Empty</div>'
        );
        assert.strictEqual(
            elements.financeLedgerContainer.innerHTML,
            '<div id="updatedLedger">CREDIT $45.00</div>'
        );
        assert.strictEqual(modalHidden, true);
        assert.strictEqual(elements.notificationToastContainer.children.length, 1);
    });

    test('duplicate reference 400 error displays inline alert without closing modal', async () => {
        const expectedErr = 'A manual deposit with this receipt reference has already been credited.';
        global.FormData = function () { return {}; };
        global.fetch = async () => ({
            ok: false,
            json: async () => ({
                success: false,
                error: expectedErr,
                errors: { reference_id: [expectedErr] },
            }),
        });

        initTeacherFinance(mockDoc);
        const submitHandler = elements.manualTopupForm.listeners.submit[0];
        await submitHandler({ preventDefault: () => {} });

        assert.strictEqual(modalHidden, false);
        assert.strictEqual(elements.manualTopupErrorAlert.textContent, expectedErr);
        assert.strictEqual(elements.manualTopupErrorAlert.classList.contains('d-none'), false);
    });

    test('clicking [data-copy-text] copies reference via clipboard and swaps icon for 1.5s', async () => {
        const copiedTexts = [];
        Object.defineProperty(global, 'navigator', {
            configurable: true,
            value: {
                clipboard: {
                    writeText: async (text) => {
                        copiedTexts.push(text);
                    },
                },
            },
        });

        const scheduledTimeouts = [];
        const origSetTimeout = global.setTimeout;
        global.setTimeout = (fn, delay) => {
            scheduledTimeouts.push({ fn, delay });
            return scheduledTimeouts.length;
        };

        try {
            const iconEl = new MockEl('I');
            iconEl.className = 'bi bi-clipboard';
            const copyBtn = new MockEl('BUTTON');
            copyBtn.dataset = { copyText: '0x1ce39062b6c846ec82368f45988ceb2e033e765cfb434af9a4c86c4a4aefca1c' };
            copyBtn.querySelector = (sel) => (sel === 'i' ? iconEl : null);

            initTeacherFinance(mockDoc);

            mockDoc.dispatchEvent({
                type: 'click',
                target: {
                    closest: (sel) => (sel === '[data-copy-text]' ? copyBtn : null),
                },
            });

            // Flush microtask from async clipboard.writeText
            await Promise.resolve();

            assert.deepStrictEqual(copiedTexts, [
                '0x1ce39062b6c846ec82368f45988ceb2e033e765cfb434af9a4c86c4a4aefca1c',
            ]);
            assert.strictEqual(iconEl.className, 'bi bi-check2 text-success');
            assert.strictEqual(scheduledTimeouts.length, 1);
            assert.strictEqual(scheduledTimeouts[0].delay, 1500);

            scheduledTimeouts[0].fn();
            assert.strictEqual(iconEl.className, 'bi bi-clipboard');
        } finally {
            global.setTimeout = origSetTimeout;
        }
    });

    test('clicking Mark Processing posts to processing endpoint, updates DOM, and shows toast', async () => {
        elements.financePayoutsContainer = new MockEl('DIV', 'financePayoutsContainer');
        elements.completePayoutModal = new MockEl('DIV', 'completePayoutModal');
        elements.completePayoutForm = new MockEl('FORM', 'completePayoutForm');
        elements.completePayoutSummary = new MockEl('DIV', 'completePayoutSummary');
        elements.completePayoutTxhash = new MockEl('INPUT', 'completePayoutTxhash');
        elements.completePayoutErrorAlert = new MockEl('DIV', 'completePayoutErrorAlert');
        elements.completePayoutSubmitBtn = new MockEl('BUTTON', 'completePayoutSubmitBtn');
        elements.rejectPayoutModal = new MockEl('DIV', 'rejectPayoutModal');
        elements.rejectPayoutForm = new MockEl('FORM', 'rejectPayoutForm');
        elements.rejectPayoutSummary = new MockEl('DIV', 'rejectPayoutSummary');
        elements.rejectPayoutReason = new MockEl('TEXTAREA', 'rejectPayoutReason');
        elements.rejectPayoutErrorAlert = new MockEl('DIV', 'rejectPayoutErrorAlert');
        elements.rejectPayoutSubmitBtn = new MockEl('BUTTON', 'rejectPayoutSubmitBtn');

        let fetchedUrl = null;
        let fetchedOptions = null;
        global.fetch = async (url, options) => {
            fetchedUrl = url;
            fetchedOptions = options;
            return {
                ok: true,
                json: async () => ({
                    success: true,
                    message: 'Payout #12 marked as processing.',
                    overview_html: '<div id="ov">Updated</div>',
                    payouts_html: '<div id="pq">PROCESSING</div>',
                    ledger_html: '<div id="lg">Ledger</div>',
                }),
            };
        };

        initTeacherFinance(mockDoc);

        const procBtn = new MockEl('BUTTON');
        procBtn.dataset = {
            actionUrl: '/dashboard/finance/payouts/12/processing/',
            payoutId: '12',
        };

        await elements.financePayoutsContainer.dispatchEvent({
            type: 'click',
            target: {
                closest: (sel) => (sel === '.btn-mark-payout-processing' ? procBtn : null),
            },
        });

        await Promise.resolve();
        await Promise.resolve();

        assert.strictEqual(fetchedUrl, '/dashboard/finance/payouts/12/processing/');
        assert.strictEqual(fetchedOptions.method, 'POST');
        assert.strictEqual(fetchedOptions.headers['X-CSRFToken'], 'test-csrf-token');
        assert.strictEqual(elements.financePayoutsContainer.innerHTML, '<div id="pq">PROCESSING</div>');
        assert.strictEqual(elements.financeOverviewContainer.innerHTML, '<div id="ov">Updated</div>');
        assert.strictEqual(elements.notificationToastContainer.children.length, 1);
    });

    test('Complete Payout modal opens with target action and submits txhash cleanly', async () => {
        elements.financePayoutsContainer = new MockEl('DIV', 'financePayoutsContainer');
        elements.completePayoutModal = new MockEl('DIV', 'completePayoutModal');
        elements.completePayoutForm = new MockEl('FORM', 'completePayoutForm');
        elements.completePayoutSummary = new MockEl('DIV', 'completePayoutSummary');
        elements.completePayoutTxhash = new MockEl('INPUT', 'completePayoutTxhash');
        elements.completePayoutErrorAlert = new MockEl('DIV', 'completePayoutErrorAlert');
        elements.completePayoutErrorAlert.classList.add('d-none');
        elements.completePayoutSubmitBtn = new MockEl('BUTTON', 'completePayoutSubmitBtn');

        let fetchedUrl = null;
        global.FormData = function (form) { return { _form: form }; };
        global.fetch = async (url) => {
            fetchedUrl = url;
            return {
                ok: true,
                json: async () => ({
                    success: true,
                    message: 'Payout #15 completed.',
                    overview_html: '<div id="ov">Settled</div>',
                    payouts_html: '<div id="pq">Empty</div>',
                    ledger_html: '<div id="lg">PAYOUT_SETTLE</div>',
                }),
            };
        };

        initTeacherFinance(mockDoc);

        const openBtn = new MockEl('BUTTON');
        openBtn.dataset = {
            actionUrl: '/dashboard/finance/payouts/15/complete/',
            payoutId: '15',
            studentName: 'Alice Student',
            amount: '30.00',
        };

        elements.financePayoutsContainer.dispatchEvent({
            type: 'click',
            target: {
                closest: (sel) => (sel === '.btn-complete-payout' ? openBtn : null),
            },
        });

        assert.strictEqual(modalShown, true);
        assert.strictEqual(elements.completePayoutForm.action, '/dashboard/finance/payouts/15/complete/');
        assert.ok(elements.completePayoutSummary.textContent.includes('Alice Student'));
        assert.ok(elements.completePayoutSummary.textContent.includes('30.00'));

        elements.completePayoutTxhash.value = '0xabc123';
        const submitHandler = elements.completePayoutForm.listeners.submit[0];
        await submitHandler({ preventDefault: () => {} });

        assert.strictEqual(fetchedUrl, '/dashboard/finance/payouts/15/complete/');
        assert.strictEqual(modalHidden, true);
        assert.strictEqual(elements.financePayoutsContainer.innerHTML, '<div id="pq">Empty</div>');
        assert.strictEqual(elements.financeLedgerContainer.innerHTML, '<div id="lg">PAYOUT_SETTLE</div>');
    });

    test('Reject Payout modal displays inline validation error without closing on 400', async () => {
        elements.financePayoutsContainer = new MockEl('DIV', 'financePayoutsContainer');
        elements.rejectPayoutModal = new MockEl('DIV', 'rejectPayoutModal');
        elements.rejectPayoutForm = new MockEl('FORM', 'rejectPayoutForm');
        elements.rejectPayoutSummary = new MockEl('DIV', 'rejectPayoutSummary');
        elements.rejectPayoutReason = new MockEl('TEXTAREA', 'rejectPayoutReason');
        elements.rejectPayoutErrorAlert = new MockEl('DIV', 'rejectPayoutErrorAlert');
        elements.rejectPayoutErrorAlert.classList.add('d-none');
        elements.rejectPayoutSubmitBtn = new MockEl('BUTTON', 'rejectPayoutSubmitBtn');

        global.FormData = function () { return {}; };
        global.fetch = async () => ({
            ok: false,
            json: async () => ({
                success: false,
                error: 'Rejection reason is required.',
            }),
        });

        initTeacherFinance(mockDoc);

        const openBtn = new MockEl('BUTTON');
        openBtn.dataset = {
            actionUrl: '/dashboard/finance/payouts/19/reject/',
            payoutId: '19',
            studentName: 'Bob Student',
            amount: '20.00',
        };

        elements.financePayoutsContainer.dispatchEvent({
            type: 'click',
            target: {
                closest: (sel) => (sel === '.btn-reject-payout' ? openBtn : null),
            },
        });

        assert.strictEqual(modalShown, true);
        assert.strictEqual(elements.rejectPayoutForm.action, '/dashboard/finance/payouts/19/reject/');

        const submitHandler = elements.rejectPayoutForm.listeners.submit[0];
        await submitHandler({ preventDefault: () => {} });

        assert.strictEqual(modalHidden, false);
        assert.strictEqual(elements.rejectPayoutErrorAlert.textContent, 'Rejection reason is required.');
        assert.strictEqual(elements.rejectPayoutErrorAlert.classList.contains('d-none'), false);
    });

    test('Mark Processing shows error toast when server returns 409 conflict', async () => {
        elements.financePayoutsContainer = new MockEl('DIV', 'financePayoutsContainer');

        global.fetch = async () => ({
            ok: false,
            json: async () => ({
                success: false,
                error: "Cannot mark payout as processing: status is 'PROCESSING', expected 'REQUESTED'.",
            }),
        });

        initTeacherFinance(mockDoc);

        const procBtn = new MockEl('BUTTON');
        procBtn.dataset = {
            actionUrl: '/dashboard/finance/payouts/12/processing/',
            payoutId: '12',
        };

        await elements.financePayoutsContainer.dispatchEvent({
            type: 'click',
            target: {
                closest: (sel) => (sel === '.btn-mark-payout-processing' ? procBtn : null),
            },
        });

        await Promise.resolve();
        await Promise.resolve();

        assert.strictEqual(elements.notificationToastContainer.children.length, 1);
        assert.strictEqual(procBtn.disabled, false);
    });

    test('formatDisputeCountdown renders remaining days/hours or Expired', () => {
        const nowMs = Date.UTC(2026, 10, 26, 12, 0, 0);
        const deadline3d5h = new Date(nowMs + ((3 * 24 + 5) * 3600 * 1000)).toISOString();
        const deadline4h20m = new Date(nowMs + ((4 * 3600 + 20 * 60) * 1000)).toISOString();
        const pastDeadline = new Date(nowMs - 60000).toISOString();

        assert.strictEqual(formatDisputeCountdown(deadline3d5h, nowMs), '3d 5h left');
        assert.strictEqual(formatDisputeCountdown(deadline4h20m, nowMs), '4h 20m left');
        assert.strictEqual(formatDisputeCountdown(pastDeadline, nowMs), 'Auto-expiring');
    });

    test('Resolve Dispute modal opens for student/teacher favor, submits POST, and updates DOM', async () => {
        elements.financeDisputesContainer = new MockEl('DIV', 'financeDisputesContainer');
        elements.resolveDisputeModal = new MockEl('DIV', 'resolveDisputeModal');
        elements.resolveDisputeForm = new MockEl('FORM', 'resolveDisputeForm');
        elements.resolveDisputeSummary = new MockEl('P', 'resolveDisputeSummary');
        elements.resolveDisputeResolution = new MockEl('INPUT', 'resolveDisputeResolution');
        elements.resolveDisputeErrorAlert = new MockEl('DIV', 'resolveDisputeErrorAlert');
        elements.resolveDisputeErrorAlert.classList.add('d-none');
        elements.resolveDisputeSubmitBtn = new MockEl('BUTTON', 'resolveDisputeSubmitBtn');

        let fetchedUrl = null;
        global.FormData = function (form) { return { _form: form }; };
        global.fetch = async (url) => {
            fetchedUrl = url;
            return {
                ok: true,
                json: async () => ({
                    success: true,
                    message: 'Dispute resolved in student favor.',
                    overview_html: '<div id="ov">Released</div>',
                    disputes_html: '<div id="dq">Empty</div>',
                    ledger_html: '<div id="lg">RELEASE</div>',
                }),
            };
        };

        initTeacherFinance(mockDoc);

        const openBtn = new MockEl('BUTTON');
        openBtn.dataset = {
            actionUrl: '/dashboard/finance/disputes/42/resolve/',
            bookingId: '42',
            studentName: 'Alice Disputer',
            amount: '25.00',
            resolution: 'STUDENT_FAVOR',
        };

        elements.financeDisputesContainer.dispatchEvent({
            type: 'click',
            target: {
                closest: (sel) => (sel === '.btn-resolve-dispute' ? openBtn : null),
            },
        });

        assert.strictEqual(modalShown, true);
        assert.strictEqual(elements.resolveDisputeForm.action, '/dashboard/finance/disputes/42/resolve/');
        assert.strictEqual(elements.resolveDisputeResolution.value, 'STUDENT_FAVOR');
        assert.ok(elements.resolveDisputeSummary.textContent.includes('Alice Disputer'));
        assert.ok(elements.resolveDisputeSummary.textContent.includes('25.00'));

        const submitHandler = elements.resolveDisputeForm.listeners.submit[0];
        await submitHandler({ preventDefault: () => {} });

        assert.strictEqual(fetchedUrl, '/dashboard/finance/disputes/42/resolve/');
        assert.strictEqual(modalHidden, true);
        assert.strictEqual(elements.financeDisputesContainer.innerHTML, '<div id="dq">Empty</div>');
        assert.strictEqual(elements.financeOverviewContainer.innerHTML, '<div id="ov">Released</div>');
        assert.strictEqual(elements.financeLedgerContainer.innerHTML, '<div id="lg">RELEASE</div>');
    });

    test('Resolve Dispute modal displays inline error on 409 without closing', async () => {
        elements.financeDisputesContainer = new MockEl('DIV', 'financeDisputesContainer');
        elements.resolveDisputeModal = new MockEl('DIV', 'resolveDisputeModal');
        elements.resolveDisputeForm = new MockEl('FORM', 'resolveDisputeForm');
        elements.resolveDisputeSummary = new MockEl('P', 'resolveDisputeSummary');
        elements.resolveDisputeResolution = new MockEl('INPUT', 'resolveDisputeResolution');
        elements.resolveDisputeErrorAlert = new MockEl('DIV', 'resolveDisputeErrorAlert');
        elements.resolveDisputeErrorAlert.classList.add('d-none');
        elements.resolveDisputeSubmitBtn = new MockEl('BUTTON', 'resolveDisputeSubmitBtn');

        global.FormData = function () { return {}; };
        global.fetch = async () => ({
            ok: false,
            json: async () => ({
                success: false,
                error: 'Booking dispute has already been resolved.',
            }),
        });

        initTeacherFinance(mockDoc);

        const openBtn = new MockEl('BUTTON');
        openBtn.dataset = {
            actionUrl: '/dashboard/finance/disputes/42/resolve/',
            bookingId: '42',
            studentName: 'Alice Disputer',
            amount: '25.00',
            resolution: 'TEACHER_FAVOR',
        };

        elements.financeDisputesContainer.dispatchEvent({
            type: 'click',
            target: {
                closest: (sel) => (sel === '.btn-resolve-dispute' ? openBtn : null),
            },
        });

        const submitHandler = elements.resolveDisputeForm.listeners.submit[0];
        await submitHandler({ preventDefault: () => {} });

        assert.strictEqual(modalHidden, false);
        assert.strictEqual(
            elements.resolveDisputeErrorAlert.textContent,
            'Booking dispute has already been resolved.'
        );
        assert.strictEqual(
            elements.resolveDisputeErrorAlert.classList.contains('d-none'),
            false
        );
    });
});
