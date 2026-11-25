const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert');

const {
    generateIdempotencyToken,
    prefillManualTopupForm,
    resetManualTopupForm,
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
            getElementById: (id) => elements[id] || null,
            createElement: (tag) => new MockEl(tag),
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
});
