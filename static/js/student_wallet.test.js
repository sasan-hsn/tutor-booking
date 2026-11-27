const { test, describe, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert');

const studentWallet = require('./student_wallet.js');

class MockClassList {
    constructor() {
        this.classes = new Set();
    }
    add(...names) {
        names.forEach((n) => this.classes.add(n));
    }
    remove(...names) {
        names.forEach((n) => this.classes.delete(n));
    }
    contains(name) {
        return this.classes.has(name);
    }
    toggle(name, force) {
        const shouldAdd = force === undefined ? !this.classes.has(name) : Boolean(force);
        if (shouldAdd) {
            this.classes.add(name);
        } else {
            this.classes.delete(name);
        }
        return shouldAdd;
    }
}

class MockEl {
    constructor(tagName = 'DIV', id = '') {
        this.tagName = tagName.toUpperCase();
        this.id = id;
        this.dataset = {};
        this.disabled = false;
        this.checked = false;
        this.value = '';
        this.src = '';
        this.textContent = '';
        this.innerHTML = '';
        this.classList = new MockClassList();
        this.listeners = {};
        this.children = [];
        this.attributes = {};
        this.tabIndex = 0;
    }
    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }
    getAttribute(name) {
        return this.attributes[name];
    }
    addEventListener(type, fn) {
        if (!this.listeners[type]) this.listeners[type] = [];
        this.listeners[type].push(fn);
    }
    async dispatchEvent(event) {
        const evt = event || { type: 'click' };
        if (!evt.target) evt.target = this;
        if (!evt.preventDefault) evt.preventDefault = () => {};
        const handlers = this.listeners[evt.type] || [];
        for (const fn of handlers) {
            await fn(evt);
        }
    }
    querySelector(selector) {
        if (selector === 'span') {
            return this._spanChild || null;
        }
        return null;
    }
}

describe('Student Wallet Top-Up Modal Controller', () => {
    let originalDocument;
    let originalWindow;
    let originalFetch;
    let originalBootstrap;
    let originalNavigator;
    let elements;
    let modalStates;
    let fetchCalls;
    let clipboardWrites;
    let termsModalOpenedWith;
    let reloaded;

    function buildEnv(options = {}) {
        modalStates = {
            topUpModal: { shown: 0, hidden: 0 },
        };
        fetchCalls = [];
        clipboardWrites = [];
        termsModalOpenedWith = null;
        reloaded = false;

        const copySpan = new MockEl('SPAN');
        copySpan.textContent = 'Copy';

        elements = {
            topUpModal: new MockEl('DIV', 'topUpModal'),
            topupErrorAlert: new MockEl('DIV', 'topupErrorAlert'),
            topupStepAmount: new MockEl('DIV', 'topupStepAmount'),
            topupStepInstructions: new MockEl('DIV', 'topupStepInstructions'),
            topupStepConfirmed: new MockEl('DIV', 'topupStepConfirmed'),
            topupInitiateForm: new MockEl('FORM', 'topupInitiateForm'),
            topupAmountInput: new MockEl('INPUT', 'topupAmountInput'),
            initiateTopupBtn: new MockEl('BUTTON', 'initiateTopupBtn'),
            topupOrderLabel: new MockEl('SPAN', 'topupOrderLabel'),
            topupOrderAmountDisplay: new MockEl('SPAN', 'topupOrderAmountDisplay'),
            topupOrderExpiresDisplay: new MockEl('SPAN', 'topupOrderExpiresDisplay'),
            topupOrderReusedNote: new MockEl('DIV', 'topupOrderReusedNote'),
            changeTopupAmountBtn: new MockEl('BUTTON', 'changeTopupAmountBtn'),
            bep20AckCheckbox: new MockEl('INPUT', 'bep20AckCheckbox'),
            topupDepositPreview: new MockEl('DIV', 'topupDepositPreview'),
            topupDepositLockOverlay: new MockEl('DIV', 'topupDepositLockOverlay'),
            topupDepositDetails: new MockEl('DIV', 'topupDepositDetails'),
            topupQrImg: new MockEl('IMG', 'topupQrImg'),
            topupDepositAddressText: new MockEl('INPUT', 'topupDepositAddressText'),
            copyTopupAddressBtn: new MockEl('BUTTON', 'copyTopupAddressBtn'),
            showClaimFormBtn: new MockEl('BUTTON', 'showClaimFormBtn'),
            topupClaimForm: new MockEl('FORM', 'topupClaimForm'),
            claimTxhashInput: new MockEl('INPUT', 'claimTxhashInput'),
            claimPaidAtInput: new MockEl('INPUT', 'claimPaidAtInput'),
            submitClaimBtn: new MockEl('BUTTON', 'submitClaimBtn'),
            topupClaimConfirmationMsg: new MockEl('P', 'topupClaimConfirmationMsg'),
            topupClaimConfirmationMeta: new MockEl('DIV', 'topupClaimConfirmationMeta'),
            navbarWalletTopupBtn: new MockEl('A', 'navbarWalletTopupBtn'),
            termsAcceptanceModal: new MockEl('DIV', 'termsAcceptanceModal'),
        };

        elements.copyTopupAddressBtn._spanChild = copySpan;
        elements.copyTopupAddressBtn.disabled = true;
        elements.showClaimFormBtn.disabled = true;

        elements.topUpModal.dataset.initiateUrl = '/wallet/topup/initiate/';
        elements.topUpModal.dataset.hasActiveAddress = options.hasActiveAddress ?? 'true';
        elements.topUpModal.dataset.minAmount = options.minAmount ?? '20.00';
        elements.topUpModal.dataset.currency = 'USD';
        elements.topUpModal.dataset.termsRequired = options.termsRequired ?? 'true';
        elements.topUpModal.dataset.termsAccepted = options.termsAccepted ?? 'true';
        elements.termsAcceptanceModal.dataset.termsVersion = '2026.1';

        elements.topupAmountInput.value = '20.00';
        elements.topupErrorAlert.classList.add('d-none');
        elements.topupStepInstructions.classList.add('d-none');
        elements.topupStepConfirmed.classList.add('d-none');
        elements.topupOrderReusedNote.classList.add('d-none');
        elements.topupDepositPreview.classList.add('topup-deposit-blurred');
        elements.topupClaimForm.classList.add('d-none');

        if (options.activeOrder) {
            const scriptEl = new MockEl('SCRIPT', 'topupActiveOrderData');
            scriptEl.textContent = JSON.stringify(options.activeOrder);
            elements.topupActiveOrderData = scriptEl;
        }

        global.document = {
            cookie: 'csrftoken=csrf-wallet-token',
            getElementById: (id) => elements[id] || null,
            querySelectorAll: () => [],
        };

        class MockModal {
            constructor(el) {
                this.el = el;
            }
            show() {
                if (!modalStates[this.el.id]) modalStates[this.el.id] = { shown: 0, hidden: 0 };
                modalStates[this.el.id].shown += 1;
            }
            hide() {
                if (!modalStates[this.el.id]) modalStates[this.el.id] = { shown: 0, hidden: 0 };
                modalStates[this.el.id].hidden += 1;
            }
            static getOrCreateInstance(el) {
                return new MockModal(el);
            }
        }

        global.bootstrap = { Modal: MockModal };
        global.window = {
            bootstrap: global.bootstrap,
            navigator: {
                clipboard: {
                    writeText: async (text) => {
                        clipboardWrites.push(text);
                    },
                },
            },
            location: {
                search: options.search || '',
                reload: () => {
                    reloaded = true;
                },
            },
            openTermsAcceptanceModal: (cb) => {
                termsModalOpenedWith = cb;
                return true;
            },
        };
    }

    beforeEach(() => {
        originalDocument = global.document;
        originalWindow = global.window;
        originalFetch = global.fetch;
        originalBootstrap = global.bootstrap;
        originalNavigator = global.navigator;
    });

    afterEach(() => {
        global.document = originalDocument;
        global.window = originalWindow;
        global.fetch = originalFetch;
        global.bootstrap = originalBootstrap;
        global.navigator = originalNavigator;
    });

    test('BEP-20 checkbox gates address, QR code, and copy button until checked', async () => {
        buildEnv();
        global.fetch = async (url, opts) => {
            fetchCalls.push({ url, opts });
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    reused: false,
                    order_id: 12,
                    amount: '30.00',
                    currency: 'USD',
                    address: '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed',
                    qr_data_uri: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
                    expires_at_display: 'Nov 28, 2026 10:00',
                    already_claimed: false,
                    claim_url: '/wallet/topup/12/claim/',
                }),
            };
        };

        studentWallet.initStudentWallet(global.document, global.window);
        elements.topupAmountInput.value = '30.00';
        await elements.topupInitiateForm.dispatchEvent({ type: 'submit' });

        assert.strictEqual(fetchCalls.length, 1);
        assert.strictEqual(elements.topupStepInstructions.classList.contains('d-none'), false);
        assert.strictEqual(elements.topupOrderAmountDisplay.textContent, '$30.00 USD');

        // Before BEP-20 checkbox is toggled: blurred preview visible with overlay, copy & claim disabled
        assert.strictEqual(elements.bep20AckCheckbox.checked, false);
        assert.strictEqual(elements.topupDepositDetails.classList.contains('d-none'), false);
        assert.strictEqual(
            elements.topupDepositPreview.classList.contains('topup-deposit-blurred'),
            true
        );
        assert.strictEqual(elements.topupDepositPreview.getAttribute('aria-hidden'), 'true');
        assert.strictEqual(elements.topupDepositLockOverlay.classList.contains('d-none'), false);
        assert.strictEqual(
            elements.topupDepositAddressText.value,
            '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed'
        );
        assert.strictEqual(
            elements.topupQrImg.src,
            'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4='
        );
        assert.strictEqual(elements.topupDepositAddressText.tabIndex, -1);
        assert.strictEqual(elements.copyTopupAddressBtn.disabled, true);
        assert.strictEqual(elements.showClaimFormBtn.disabled, true);

        await elements.copyTopupAddressBtn.dispatchEvent({ type: 'click' });
        assert.strictEqual(clipboardWrites.length, 0);
        await elements.showClaimFormBtn.dispatchEvent({ type: 'click' });
        assert.strictEqual(elements.topupClaimForm.classList.contains('d-none'), true);

        // Check BEP-20 acknowledgment checkbox: unblurs preview, removes overlay, enables copy & claim
        elements.bep20AckCheckbox.checked = true;
        await elements.bep20AckCheckbox.dispatchEvent({ type: 'change' });

        assert.strictEqual(
            elements.topupDepositPreview.classList.contains('topup-deposit-blurred'),
            false
        );
        assert.strictEqual(elements.topupDepositPreview.getAttribute('aria-hidden'), 'false');
        assert.strictEqual(elements.topupDepositLockOverlay.classList.contains('d-none'), true);
        assert.strictEqual(elements.topupDepositAddressText.tabIndex, 0);
        assert.strictEqual(elements.copyTopupAddressBtn.disabled, false);
        assert.strictEqual(elements.showClaimFormBtn.disabled, false);

        await elements.copyTopupAddressBtn.dispatchEvent({ type: 'click' });
        assert.deepStrictEqual(clipboardWrites, ['0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed']);
        assert.strictEqual(elements.copyTopupAddressBtn._spanChild.textContent, 'Copied!');

        // Unchecking re-blurs preview, restores overlay, and re-disables copy & claim
        elements.bep20AckCheckbox.checked = false;
        await elements.bep20AckCheckbox.dispatchEvent({ type: 'change' });
        assert.strictEqual(
            elements.topupDepositPreview.classList.contains('topup-deposit-blurred'),
            true
        );
        assert.strictEqual(elements.topupDepositLockOverlay.classList.contains('d-none'), false);
        assert.strictEqual(elements.copyTopupAddressBtn.disabled, true);
        assert.strictEqual(elements.showClaimFormBtn.disabled, true);
    });

    test('reused order displays actual backend order amount and minimum validation blocks low amounts', async () => {
        buildEnv();
        global.fetch = async (url) => {
            fetchCalls.push({ url });
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    reused: true,
                    order_id: 77,
                    amount: '25.00',
                    currency: 'USD',
                    address: '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed',
                    qr_data_uri: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
                    expires_at_display: 'Nov 28, 2026 10:00',
                    already_claimed: false,
                    claim_url: '/wallet/topup/77/claim/',
                }),
            };
        };

        studentWallet.initStudentWallet(global.document, global.window);

        // Client-side minimum check blocks < $20.00
        elements.topupAmountInput.value = '10.00';
        await elements.topupInitiateForm.dispatchEvent({ type: 'submit' });
        assert.strictEqual(fetchCalls.length, 0);
        assert.strictEqual(elements.topupErrorAlert.classList.contains('d-none'), false);
        assert.ok(elements.topupErrorAlert.textContent.includes('20.00'));

        // Student enters 50.00, backend reuses existing 25.00 order -> UI shows $25.00 USD
        elements.topupAmountInput.value = '50.00';
        await elements.topupInitiateForm.dispatchEvent({ type: 'submit' });
        assert.strictEqual(fetchCalls.length, 1);
        assert.strictEqual(elements.topupOrderAmountDisplay.textContent, '$25.00 USD');
        assert.strictEqual(elements.topupOrderReusedNote.classList.contains('d-none'), false);
    });

    test('terms gate intercepts unaccepted initiation and resumes after acceptance', async () => {
        buildEnv({ termsRequired: 'true', termsAccepted: 'false' });
        global.fetch = async (url) => {
            fetchCalls.push({ url });
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    reused: false,
                    order_id: 91,
                    amount: '20.00',
                    currency: 'USD',
                    address: '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed',
                    qr_data_uri: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
                    expires_at_display: 'Nov 28, 2026 10:00',
                    already_claimed: false,
                    claim_url: '/wallet/topup/91/claim/',
                }),
            };
        };

        studentWallet.initStudentWallet(global.document, global.window);
        await elements.topupInitiateForm.dispatchEvent({ type: 'submit' });

        assert.strictEqual(fetchCalls.length, 0);
        assert.strictEqual(modalStates.topUpModal.hidden, 1);
        assert.strictEqual(typeof termsModalOpenedWith, 'function');

        // Simulate terms modal acceptance callback resuming top-up initiation
        elements.topUpModal.dataset.termsAccepted = 'true';
        await termsModalOpenedWith();
        assert.strictEqual(fetchCalls.length, 1);
        assert.strictEqual(modalStates.topUpModal.shown, 1);
        assert.strictEqual(elements.topupStepInstructions.classList.contains('d-none'), false);
    });

    test('I Have Paid flow submits claim and displays confirmation state', async () => {
        buildEnv();
        global.fetch = async (url, opts) => {
            fetchCalls.push({ url, opts });
            if (url === '/wallet/topup/initiate/') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        success: true,
                        reused: false,
                        order_id: 55,
                        amount: '40.00',
                        currency: 'USD',
                        address: '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed',
                        qr_data_uri: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
                        expires_at_display: 'Nov 28, 2026 10:00',
                        already_claimed: false,
                        claim_url: '/wallet/topup/55/claim/',
                    }),
                };
            }
            if (url === '/wallet/topup/55/claim/') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        success: true,
                        order_id: 55,
                        claimed_txhash: '0xclaim789',
                        message:
                            'Your payment claim has been submitted. The teacher will verify the deposit and credit your account shortly.',
                    }),
                };
            }
            throw new Error(`Unexpected URL ${url}`);
        };

        studentWallet.initStudentWallet(global.document, global.window);
        elements.topupAmountInput.value = '40.00';
        await elements.topupInitiateForm.dispatchEvent({ type: 'submit' });

        assert.strictEqual(elements.showClaimFormBtn.disabled, true);
        elements.bep20AckCheckbox.checked = true;
        await elements.bep20AckCheckbox.dispatchEvent({ type: 'change' });
        assert.strictEqual(elements.showClaimFormBtn.disabled, false);

        await elements.showClaimFormBtn.dispatchEvent({ type: 'click' });
        assert.strictEqual(elements.topupClaimForm.classList.contains('d-none'), false);

        elements.claimTxhashInput.value = '0xclaim789';
        elements.claimPaidAtInput.value = '2026-11-27T10:00';
        await elements.topupClaimForm.dispatchEvent({ type: 'submit' });

        assert.strictEqual(fetchCalls.length, 2);
        assert.strictEqual(fetchCalls[1].url, '/wallet/topup/55/claim/');
        assert.strictEqual(elements.topupStepInstructions.classList.contains('d-none'), true);
        assert.strictEqual(elements.topupStepConfirmed.classList.contains('d-none'), false);
        assert.ok(
            elements.topupClaimConfirmationMsg.textContent.includes(
                'Your payment claim has been submitted.'
            )
        );
    });

    test('?topup=1 auto-opens modal and already-claimed active order renders confirmed view', async () => {
        buildEnv({
            search: '?topup=1',
            activeOrder: {
                order_id: 88,
                amount: '50.00',
                currency: 'USD',
                expires_at_display: 'Nov 28, 2026 10:00',
                already_claimed: true,
                claimed_txhash: '0xexistingclaim',
                claim_url: '/wallet/topup/88/claim/',
            },
        });

        studentWallet.initStudentWallet(global.document, global.window);
        assert.strictEqual(modalStates.topUpModal.shown, 1);
        assert.strictEqual(elements.topupStepConfirmed.classList.contains('d-none'), false);
        assert.ok(elements.topupClaimConfirmationMeta.textContent.includes('0xexistingclaim'));
        assert.strictEqual(reloaded, false);
    });

    test('?topup=1 with unaccepted terms opens terms modal first and reveals top-up modal on accept', async () => {
        buildEnv({
            search: '?topup=1',
            termsRequired: 'true',
            termsAccepted: 'false',
        });

        studentWallet.initStudentWallet(global.document, global.window);
        assert.strictEqual(modalStates.topUpModal.shown, 0);
        assert.strictEqual(typeof termsModalOpenedWith, 'function');

        await termsModalOpenedWith();
        assert.strictEqual(elements.topUpModal.dataset.termsAccepted, 'true');
        assert.strictEqual(modalStates.topUpModal.shown, 1);
    });

    test('Change amount cancels unclaimed pending order, resets state, and returns to amount input step', async () => {
        buildEnv({
            activeOrder: {
                order_id: 66,
                amount: '25.00',
                currency: 'USD',
                expires_at_display: 'Nov 28, 2026 10:00',
                already_claimed: false,
                claimed_txhash: '',
                claim_url: '/wallet/topup/66/claim/',
                cancel_url: '/wallet/topup/66/cancel/',
            },
        });

        let initiateCount = 0;
        global.fetch = async (url, opts) => {
            fetchCalls.push({ url, opts });
            if (url === '/wallet/topup/initiate/') {
                initiateCount += 1;
                if (initiateCount === 1) {
                    return {
                        ok: true,
                        status: 200,
                        json: async () => ({
                            success: true,
                            reused: true,
                            order_id: 66,
                            amount: '25.00',
                            currency: 'USD',
                            address: '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed',
                            qr_data_uri: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
                            expires_at_display: 'Nov 28, 2026 10:00',
                            already_claimed: false,
                            claim_url: '/wallet/topup/66/claim/',
                            cancel_url: '/wallet/topup/66/cancel/',
                        }),
                    };
                }
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        success: true,
                        reused: false,
                        order_id: 67,
                        amount: '50.00',
                        currency: 'USD',
                        address: '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed',
                        qr_data_uri: 'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
                        expires_at_display: 'Nov 28, 2026 11:00',
                        already_claimed: false,
                        claim_url: '/wallet/topup/67/claim/',
                        cancel_url: '/wallet/topup/67/cancel/',
                    }),
                };
            }
            if (url === '/wallet/topup/66/cancel/') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        success: true,
                        order_id: 66,
                        status: 'CANCELLED',
                    }),
                };
            }
            throw new Error(`Unexpected URL ${url}`);
        };

        studentWallet.initStudentWallet(global.document, global.window);
        await elements.topupInitiateForm.dispatchEvent({ type: 'submit' });

        // Check BEP-20 checkbox first to verify Change amount resets it cleanly
        elements.bep20AckCheckbox.checked = true;
        await elements.bep20AckCheckbox.dispatchEvent({ type: 'change' });
        assert.strictEqual(elements.changeTopupAmountBtn.classList.contains('d-none'), false);

        await elements.changeTopupAmountBtn.dispatchEvent({ type: 'click' });

        assert.strictEqual(fetchCalls.length, 2);
        assert.strictEqual(fetchCalls[1].url, '/wallet/topup/66/cancel/');
        assert.strictEqual(elements.topupStepAmount.classList.contains('d-none'), false);
        assert.strictEqual(elements.topupStepInstructions.classList.contains('d-none'), true);
        assert.strictEqual(elements.bep20AckCheckbox.checked, false);
        assert.strictEqual(elements.topupDepositAddressText.value, '');
        assert.strictEqual(elements.topupQrImg.src, '');
        assert.strictEqual(elements.topupActiveOrderData.textContent, '');

        // Now submitting a lower/new amount validates normally and creates a fresh order
        elements.topupAmountInput.value = '50.00';
        await elements.topupInitiateForm.dispatchEvent({ type: 'submit' });
        assert.strictEqual(fetchCalls.length, 3);
        assert.strictEqual(elements.topupOrderAmountDisplay.textContent, '$50.00 USD');
        assert.strictEqual(elements.topupOrderLabel.textContent, '#67');
    });

    test('refund modal validates over-withdrawal client-side and submits valid Stage 1 request', async () => {
        buildEnv();
        const refundModal = new MockEl('DIV', 'refundModal');
        refundModal.dataset.requestUrl = '/wallet/payouts/request/';
        refundModal.dataset.availableBalance = '50.00';
        refundModal.dataset.currency = 'USD';
        refundModal.dataset.isFrozen = 'false';

        const refundErrorAlert = new MockEl('DIV', 'refundErrorAlert');
        refundErrorAlert.classList.add('d-none');
        const refundStepForm = new MockEl('DIV', 'refundStepForm');
        const refundStepSubmitted = new MockEl('DIV', 'refundStepSubmitted');
        refundStepSubmitted.classList.add('d-none');
        const refundRequestForm = new MockEl('FORM', 'refundRequestForm');
        const refundAmountInput = new MockEl('INPUT', 'refundAmountInput');
        const refundDestinationInput = new MockEl('INPUT', 'refundDestinationInput');
        const refundNotesInput = new MockEl('INPUT', 'refundNotesInput');
        const submitRefundRequestBtn = new MockEl('BUTTON', 'submitRefundRequestBtn');
        const refundConfirmationNotice = new MockEl('P', 'refundConfirmationNotice');
        const refundSubmittedMeta = new MockEl('DIV', 'refundSubmittedMeta');
        const studentPayoutsContainer = new MockEl('SECTION', 'studentPayoutsContainer');

        Object.assign(elements, {
            refundModal,
            refundErrorAlert,
            refundStepForm,
            refundStepSubmitted,
            refundRequestForm,
            refundAmountInput,
            refundDestinationInput,
            refundNotesInput,
            submitRefundRequestBtn,
            refundConfirmationNotice,
            refundSubmittedMeta,
            studentPayoutsContainer,
        });

        global.fetch = async (url, opts) => {
            fetchCalls.push({ url, opts });
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    payout_id: 91,
                    status: 'PENDING_CONFIRMATION',
                    amount_gross: '25.00',
                    currency: 'USD',
                    message: 'We have sent a confirmation link to your email. You must confirm this request within 24 hours.',
                    payouts_html: '<div id="payout-91">Pending</div>',
                }),
            };
        };

        studentWallet.initStudentWallet(global.document, global.window);

        // Over-withdrawal blocked client-side without fetch
        refundAmountInput.value = '75.00';
        refundDestinationInput.value = '0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed';
        await refundRequestForm.dispatchEvent({ type: 'submit' });
        assert.strictEqual(fetchCalls.length, 0);
        assert.strictEqual(refundErrorAlert.classList.contains('d-none'), false);
        assert.match(refundErrorAlert.textContent, /cannot exceed/i);

        // Valid submission posts to Stage 1 endpoint and shows 24h notice
        refundAmountInput.value = '25.00';
        refundNotesInput.value = 'Returning unused credit';
        await refundRequestForm.dispatchEvent({ type: 'submit' });

        assert.strictEqual(fetchCalls.length, 1);
        assert.strictEqual(fetchCalls[0].url, '/wallet/payouts/request/');
        assert.strictEqual(refundStepForm.classList.contains('d-none'), true);
        assert.strictEqual(refundStepSubmitted.classList.contains('d-none'), false);
        assert.match(refundConfirmationNotice.textContent, /24 hours/);
        assert.strictEqual(studentPayoutsContainer.innerHTML, '<div id="payout-91">Pending</div>');
    });

    test('payout cancellation updates balances and handles 409 conflict', async () => {
        buildEnv();
        const studentPayoutsContainer = new MockEl('SECTION', 'studentPayoutsContainer');
        const studentPayoutsAlert = new MockEl('DIV', 'studentPayoutsAlert');
        studentPayoutsAlert.classList.add('d-none');
        const walletAvailableBalanceValue = new MockEl('DIV', 'walletAvailableBalanceValue');
        walletAvailableBalanceValue.textContent = '$25.00';
        const walletReservedBalanceValue = new MockEl('DIV', 'walletReservedBalanceValue');
        walletReservedBalanceValue.textContent = '$25.00';
        const walletSettledBalanceValue = new MockEl('DIV', 'walletSettledBalanceValue');
        walletSettledBalanceValue.textContent = '$50.00';

        Object.assign(elements, {
            studentPayoutsContainer,
            studentPayoutsAlert,
            walletAvailableBalanceValue,
            walletReservedBalanceValue,
            walletSettledBalanceValue,
        });

        let shouldConflict = false;
        global.fetch = async (url, opts) => {
            fetchCalls.push({ url, opts });
            if (shouldConflict) {
                return {
                    ok: false,
                    status: 409,
                    json: async () => ({
                        success: false,
                        error: "Cannot cancel payout in 'PROCESSING' status.",
                    }),
                };
            }
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    payout_id: 91,
                    status: 'CANCELLED',
                    message: 'Refund request #91 cancelled and $25.00 USD released back to your available balance.',
                    available_balance: '50.00',
                    reserved_balance: '0.00',
                    settled_balance: '50.00',
                    payouts_html: '<div id="payout-91">Cancelled</div>',
                }),
            };
        };

        studentWallet.initStudentWallet(global.document, global.window);

        const cancelForm = new MockEl('FORM', 'cancelForm91');
        cancelForm.classList.add('student-payout-cancel-form');
        cancelForm.setAttribute('action', '/wallet/payouts/91/cancel/');
        const cancelBtn = new MockEl('BUTTON');
        cancelForm.querySelector = () => cancelBtn;

        await studentPayoutsContainer.dispatchEvent({ type: 'submit', target: cancelForm });
        assert.strictEqual(fetchCalls.length, 1);
        assert.strictEqual(fetchCalls[0].url, '/wallet/payouts/91/cancel/');
        assert.strictEqual(walletAvailableBalanceValue.textContent, '$50.00');
        assert.strictEqual(walletReservedBalanceValue.textContent, '$0.00');
        assert.strictEqual(studentPayoutsAlert.classList.contains('alert-success'), true);

        shouldConflict = true;
        await studentPayoutsContainer.dispatchEvent({ type: 'submit', target: cancelForm });
        assert.strictEqual(studentPayoutsAlert.classList.contains('alert-danger'), true);
        assert.match(studentPayoutsAlert.textContent, /PROCESSING/);
    });
});
