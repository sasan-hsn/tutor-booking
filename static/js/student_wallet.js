/**
 * student_wallet.js - Student Wallet Top-Up Modal controller handling
 * Terms Acceptance Gate, order creation/reuse, BEP-20 network acknowledgment
 * gating, clipboard copy feedback, and "I Have Paid" claim submission.
 */

(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.StudentWallet = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const DEFAULT_CLAIM_CONFIRMATION_MSG =
        'Your payment claim has been submitted. The teacher will verify the deposit and credit your account shortly.';

    function getCsrfToken(doc) {
        if (!doc || !doc.cookie) return '';
        const cookies = doc.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, 10) === 'csrftoken=') {
                return decodeURIComponent(cookie.substring(10));
            }
        }
        return '';
    }

    function setAlert(alertEl, message, variantClass) {
        if (!alertEl) return;
        alertEl.textContent = message || '';
        if (variantClass && alertEl.classList) {
            alertEl.classList.remove('alert-danger', 'alert-success', 'alert-warning', 'alert-info');
            alertEl.classList.add(variantClass);
        }
        if (message) {
            alertEl.classList.remove('d-none');
        } else {
            alertEl.classList.add('d-none');
        }
    }

    async function postWalletForm(doc, url, body, buttonEl, busyLabel, defaultLabel, alertEl) {
        setAlert(alertEl, '');
        if (buttonEl) {
            buttonEl.disabled = true;
            buttonEl.textContent = busyLabel;
        }

        let response;
        let data = null;
        try {
            response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Accept': 'application/json',
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': getCsrfToken(doc),
                },
                body: body,
            });
            try {
                data = await response.json();
            } catch (_) {
                data = null;
            }
        } catch (_) {
            setAlert(alertEl, 'Network error. Please try again.', 'alert-danger');
            return { networkError: true, ok: false, status: 0, data: null };
        } finally {
            if (buttonEl) {
                buttonEl.disabled = false;
                buttonEl.textContent = defaultLabel;
            }
        }

        return {
            networkError: false,
            ok: response.ok && Boolean(data && data.success),
            status: response.status,
            data: data,
        };
    }

    function toggleModal(modalEl, win, action) {
        if (
            modalEl
            && win
            && win.bootstrap
            && win.bootstrap.Modal
            && typeof win.bootstrap.Modal.getOrCreateInstance === 'function'
        ) {
            const instance = win.bootstrap.Modal.getOrCreateInstance(modalEl);
            if (instance && typeof instance[action] === 'function') {
                instance[action]();
            }
        }
    }

    function parseActiveOrderData(doc) {
        const scriptEl = doc.getElementById('topupActiveOrderData');
        if (!scriptEl || !scriptEl.textContent) return null;
        try {
            return JSON.parse(scriptEl.textContent);
        } catch (_) {
            return null;
        }
    }

    function hasTopupQueryParam(win) {
        const search = (win && win.location && win.location.search) || '';
        if (!search) return false;
        const params = new URLSearchParams(search);
        const val = params.get('topup');
        return val === '1' || val === 'true';
    }

    function initStudentWallet(doc, win) {
        const documentRef = doc || (typeof document !== 'undefined' ? document : null);
        const windowRef = win || (typeof window !== 'undefined' ? window : null);
        if (!documentRef) return null;

        const modalEl = documentRef.getElementById('topUpModal');
        if (!modalEl) return null;

        if (modalEl.dataset.walletModalBound === 'true') return null;
        modalEl.dataset.walletModalBound = 'true';

        const elements = {
            modalEl: modalEl,
            errorAlert: documentRef.getElementById('topupErrorAlert'),
            stepAmount: documentRef.getElementById('topupStepAmount'),
            stepInstructions: documentRef.getElementById('topupStepInstructions'),
            stepConfirmed: documentRef.getElementById('topupStepConfirmed'),
            initiateForm: documentRef.getElementById('topupInitiateForm'),
            amountInput: documentRef.getElementById('topupAmountInput'),
            initiateBtn: documentRef.getElementById('initiateTopupBtn'),
            orderLabel: documentRef.getElementById('topupOrderLabel'),
            orderAmountDisplay: documentRef.getElementById('topupOrderAmountDisplay'),
            orderExpiresDisplay: documentRef.getElementById('topupOrderExpiresDisplay'),
            orderReusedNote: documentRef.getElementById('topupOrderReusedNote'),
            changeAmountBtn: documentRef.getElementById('changeTopupAmountBtn'),
            bep20Checkbox: documentRef.getElementById('bep20AckCheckbox'),
            depositPreview: documentRef.getElementById('topupDepositPreview'),
            depositLockOverlay: documentRef.getElementById('topupDepositLockOverlay'),
            depositDetails: documentRef.getElementById('topupDepositDetails'),
            qrImg: documentRef.getElementById('topupQrImg'),
            addressInput: documentRef.getElementById('topupDepositAddressText'),
            copyBtn: documentRef.getElementById('copyTopupAddressBtn'),
            showClaimFormBtn: documentRef.getElementById('showClaimFormBtn'),
            claimForm: documentRef.getElementById('topupClaimForm'),
            claimTxhashInput: documentRef.getElementById('claimTxhashInput'),
            claimPaidAtInput: documentRef.getElementById('claimPaidAtInput'),
            submitClaimBtn: documentRef.getElementById('submitClaimBtn'),
            confirmationMsg: documentRef.getElementById('topupClaimConfirmationMsg'),
            confirmationMeta: documentRef.getElementById('topupClaimConfirmationMeta'),
            navbarTopupBtn: documentRef.getElementById('navbarWalletTopupBtn'),
        };

        const state = {
            currentOrder: null,
            copyTimer: null,
        };

        function showOnlyStep(stepName) {
            if (elements.stepAmount) {
                elements.stepAmount.classList.toggle('d-none', stepName !== 'amount');
            }
            if (elements.stepInstructions) {
                elements.stepInstructions.classList.toggle('d-none', stepName !== 'instructions');
            }
            if (elements.stepConfirmed) {
                elements.stepConfirmed.classList.toggle('d-none', stepName !== 'confirmed');
            }
        }

        function syncBep20Gate() {
            const isChecked = Boolean(elements.bep20Checkbox && elements.bep20Checkbox.checked);
            const hasOrder = Boolean(state.currentOrder && state.currentOrder.address);
            const unlocked = isChecked && hasOrder;

            if (elements.depositPreview) {
                elements.depositPreview.classList.toggle('topup-deposit-blurred', !unlocked);
                if (typeof elements.depositPreview.setAttribute === 'function') {
                    elements.depositPreview.setAttribute('aria-hidden', unlocked ? 'false' : 'true');
                }
            }
            if (elements.depositLockOverlay) {
                elements.depositLockOverlay.classList.toggle('d-none', unlocked);
            }
            if (elements.addressInput) {
                elements.addressInput.tabIndex = unlocked ? 0 : -1;
            }
            if (elements.copyBtn) {
                elements.copyBtn.disabled = !unlocked;
            }
            if (elements.showClaimFormBtn) {
                elements.showClaimFormBtn.disabled = !unlocked;
                if (!unlocked) {
                    elements.showClaimFormBtn.classList.remove('d-none');
                }
            }
            if (!unlocked && elements.claimForm) {
                elements.claimForm.classList.add('d-none');
            }
        }

        function resetCopyButtonLabel() {
            if (!elements.copyBtn) return;
            const span = elements.copyBtn.querySelector('span');
            if (span) span.textContent = 'Copy';
        }

        function renderConfirmedState(orderData, message) {
            setAlert(elements.errorAlert, '');
            if (elements.confirmationMsg && message) {
                elements.confirmationMsg.textContent = message;
            }
            if (elements.confirmationMeta && orderData) {
                const txPart = orderData.claimed_txhash
                    ? ` · Reference: ${orderData.claimed_txhash}`
                    : '';
                elements.confirmationMeta.textContent =
                    `Order #${orderData.order_id} · $${orderData.amount} ${orderData.currency || 'USD'}${txPart}`;
            }
            showOnlyStep('confirmed');
        }

        function renderInstructionsState(orderData) {
            state.currentOrder = orderData;
            setAlert(elements.errorAlert, '');

            if (elements.orderLabel) {
                elements.orderLabel.textContent = `#${orderData.order_id}`;
            }
            if (elements.orderAmountDisplay) {
                elements.orderAmountDisplay.textContent = `$${orderData.amount} ${orderData.currency}`;
            }
            if (elements.orderExpiresDisplay) {
                elements.orderExpiresDisplay.textContent = orderData.expires_at_display || '24 hours';
            }
            if (elements.orderReusedNote) {
                elements.orderReusedNote.classList.toggle('d-none', !orderData.reused);
            }
            if (elements.changeAmountBtn) {
                const canChange = !orderData.already_claimed && Boolean(orderData.cancel_url);
                elements.changeAmountBtn.classList.toggle('d-none', !canChange);
            }
            if (elements.amountInput && orderData.amount) {
                elements.amountInput.value = orderData.amount;
            }
            if (elements.addressInput) {
                elements.addressInput.value = orderData.address || '';
            }
            if (elements.qrImg) {
                elements.qrImg.src = orderData.qr_data_uri || '';
            }
            if (elements.bep20Checkbox) {
                elements.bep20Checkbox.checked = false;
            }
            if (elements.claimForm) {
                elements.claimForm.classList.add('d-none');
            }
            if (elements.showClaimFormBtn) {
                elements.showClaimFormBtn.classList.remove('d-none');
            }
            resetCopyButtonLabel();
            syncBep20Gate();
            showOnlyStep('instructions');
        }

        function getOpenTermsFn() {
            if (windowRef && typeof windowRef.openTermsAcceptanceModal === 'function') {
                return windowRef.openTermsAcceptanceModal;
            }
            if (typeof openTermsAcceptanceModal === 'function') {
                return openTermsAcceptanceModal;
            }
            return null;
        }

        function handleTermsNotAccepted(data, resumeFn) {
            modalEl.dataset.termsAccepted = 'false';
            const termsModalEl = documentRef.getElementById('termsAcceptanceModal');
            const renderedVersion = termsModalEl ? termsModalEl.dataset.termsVersion : '';
            if (
                data
                && data.terms_version
                && renderedVersion
                && data.terms_version !== renderedVersion
            ) {
                if (windowRef && windowRef.location && typeof windowRef.location.reload === 'function') {
                    windowRef.location.reload();
                }
                return true;
            }

            const openTermsFn = getOpenTermsFn();
            if (!openTermsFn) return false;

            toggleModal(modalEl, windowRef, 'hide');
            return openTermsFn(async function () {
                toggleModal(modalEl, windowRef, 'show');
                await resumeFn();
            });
        }

        function postTopupForm(url, body, buttonEl, busyLabel, defaultLabel) {
            return postWalletForm(
                documentRef,
                url,
                body,
                buttonEl,
                busyLabel,
                defaultLabel,
                elements.errorAlert
            );
        }

        async function submitInitiateOrder() {
            const existingOrder = parseActiveOrderData(documentRef);
            if (modalEl.dataset.hasActiveAddress !== 'true' && !existingOrder) {
                setAlert(
                    elements.errorAlert,
                    'Deposits are temporarily unavailable because no active receiving address is configured.'
                );
                return;
            }

            const rawAmount = elements.amountInput ? elements.amountInput.value.trim() : '';
            const parsedAmount = Number.parseFloat(rawAmount);
            const minAmount = Number.parseFloat(modalEl.dataset.minAmount || '20.00');
            const currency = modalEl.dataset.currency || 'USD';

            if (!existingOrder) {
                if (!rawAmount || Number.isNaN(parsedAmount) || parsedAmount <= 0) {
                    setAlert(elements.errorAlert, 'Please enter a valid deposit amount.');
                    return;
                }
                if (parsedAmount < minAmount) {
                    setAlert(
                        elements.errorAlert,
                        `Minimum top-up amount is $${minAmount.toFixed(2)} ${currency}.`
                    );
                    return;
                }
            }

            const initiateUrl = modalEl.dataset.initiateUrl;
            if (!initiateUrl) return;

            const result = await postTopupForm(
                initiateUrl,
                `amount=${encodeURIComponent(rawAmount)}`,
                elements.initiateBtn,
                'Preparing Order...',
                'Continue to Deposit Instructions'
            );
            if (elements.initiateBtn && modalEl.dataset.hasActiveAddress !== 'true' && !existingOrder) {
                elements.initiateBtn.disabled = true;
            }
            if (result.networkError) return;

            if (!result.ok) {
                if (result.status === 403 && result.data && result.data.error === 'terms_not_accepted') {
                    if (handleTermsNotAccepted(result.data, submitInitiateOrder)) {
                        return;
                    }
                }
                const errorMsg = (result.data && result.data.error)
                    ? result.data.error
                    : 'Unable to prepare your top-up order. Please try again.';
                setAlert(elements.errorAlert, errorMsg);
                return;
            }

            if (result.data.already_claimed) {
                renderConfirmedState(result.data, DEFAULT_CLAIM_CONFIRMATION_MSG);
                return;
            }

            renderInstructionsState(result.data);
        }

        async function submitPaymentClaim() {
            if (
                !state.currentOrder
                || !state.currentOrder.claim_url
                || !elements.bep20Checkbox
                || !elements.bep20Checkbox.checked
            ) {
                return;
            }

            const txhash = elements.claimTxhashInput ? elements.claimTxhashInput.value.trim() : '';
            const paidAt = elements.claimPaidAtInput ? elements.claimPaidAtInput.value.trim() : '';
            const body = `txhash=${encodeURIComponent(txhash)}&paid_at=${encodeURIComponent(paidAt)}`;

            const result = await postTopupForm(
                state.currentOrder.claim_url,
                body,
                elements.submitClaimBtn,
                'Submitting...',
                'Submit Payment Claim'
            );
            if (result.networkError) return;

            if (!result.ok) {
                if (result.status === 403 && result.data && result.data.error === 'terms_not_accepted') {
                    if (handleTermsNotAccepted(result.data, submitPaymentClaim)) {
                        return;
                    }
                }
                const errorMsg = (result.data && result.data.error)
                    ? result.data.error
                    : 'Unable to submit your payment claim. Please try again.';
                setAlert(elements.errorAlert, errorMsg);
                return;
            }

            renderConfirmedState(
                {
                    order_id: result.data.order_id || state.currentOrder.order_id,
                    amount: state.currentOrder.amount,
                    currency: state.currentOrder.currency,
                    claimed_txhash: result.data.claimed_txhash || txhash,
                },
                result.data.message
            );
        }

        async function cancelAndChangeAmount() {
            if (!state.currentOrder || !state.currentOrder.cancel_url) return;

            const result = await postTopupForm(
                state.currentOrder.cancel_url,
                '',
                elements.changeAmountBtn,
                'Cancelling...',
                'Change amount'
            );
            if (result.networkError) return;

            if (!result.ok) {
                if (result.status === 403 && result.data && result.data.error === 'terms_not_accepted') {
                    if (handleTermsNotAccepted(result.data, cancelAndChangeAmount)) {
                        return;
                    }
                }
                const errorMsg = (result.data && result.data.error)
                    ? result.data.error
                    : 'Unable to cancel this order. Please try again.';
                setAlert(elements.errorAlert, errorMsg);
                return;
            }

            state.currentOrder = null;
            const activeOrderScript = documentRef.getElementById('topupActiveOrderData');
            if (activeOrderScript) {
                activeOrderScript.textContent = '';
            }
            if (elements.bep20Checkbox) {
                elements.bep20Checkbox.checked = false;
            }
            if (elements.addressInput) {
                elements.addressInput.value = '';
            }
            if (elements.qrImg) {
                elements.qrImg.src = '';
            }
            if (elements.orderReusedNote) {
                elements.orderReusedNote.classList.add('d-none');
            }
            if (elements.claimTxhashInput) {
                elements.claimTxhashInput.value = '';
            }
            if (elements.claimPaidAtInput) {
                elements.claimPaidAtInput.value = '';
            }

            const hasActiveAddress = modalEl.dataset.hasActiveAddress === 'true';
            if (elements.amountInput) {
                elements.amountInput.disabled = !hasActiveAddress;
            }
            if (elements.initiateBtn) {
                elements.initiateBtn.disabled = !hasActiveAddress;
            }

            resetCopyButtonLabel();
            syncBep20Gate();
            setAlert(elements.errorAlert, '');
            showOnlyStep('amount');
        }

        if (elements.initiateForm) {
            elements.initiateForm.addEventListener('submit', async function (e) {
                if (e && typeof e.preventDefault === 'function') {
                    e.preventDefault();
                }

                if (
                    modalEl.dataset.termsRequired === 'true'
                    && modalEl.dataset.termsAccepted !== 'true'
                ) {
                    const openTermsFn = getOpenTermsFn();
                    if (openTermsFn) {
                        toggleModal(modalEl, windowRef, 'hide');
                        openTermsFn(async function () {
                            toggleModal(modalEl, windowRef, 'show');
                            await submitInitiateOrder();
                        });
                        return;
                    }
                }

                await submitInitiateOrder();
            });
        }

        if (elements.bep20Checkbox) {
            elements.bep20Checkbox.addEventListener('change', syncBep20Gate);
        }

        if (elements.changeAmountBtn) {
            elements.changeAmountBtn.addEventListener('click', cancelAndChangeAmount);
        }

        if (elements.copyBtn) {
            elements.copyBtn.addEventListener('click', async function () {
                if (
                    elements.copyBtn.disabled
                    || !elements.bep20Checkbox
                    || !elements.bep20Checkbox.checked
                    || !state.currentOrder
                    || !state.currentOrder.address
                ) {
                    return;
                }

                const nav = (windowRef && windowRef.navigator)
                    || (typeof navigator !== 'undefined' ? navigator : null);
                const clipboard = (nav && nav.clipboard) ? nav.clipboard : null;
                if (!clipboard || typeof clipboard.writeText !== 'function') return;

                try {
                    await clipboard.writeText(state.currentOrder.address);
                } catch (_) {
                    return;
                }

                const span = elements.copyBtn.querySelector('span');
                if (span) {
                    if (state.copyTimer) clearTimeout(state.copyTimer);
                    span.textContent = 'Copied!';
                    state.copyTimer = setTimeout(function () {
                        resetCopyButtonLabel();
                        state.copyTimer = null;
                    }, 2000);
                }
            });
        }

        if (elements.showClaimFormBtn) {
            elements.showClaimFormBtn.addEventListener('click', function () {
                if (
                    elements.showClaimFormBtn.disabled
                    || !elements.bep20Checkbox
                    || !elements.bep20Checkbox.checked
                ) {
                    return;
                }
                if (elements.claimForm) {
                    elements.claimForm.classList.remove('d-none');
                }
                elements.showClaimFormBtn.classList.add('d-none');
            });
        }

        if (elements.claimForm) {
            elements.claimForm.addEventListener('submit', async function (e) {
                if (e && typeof e.preventDefault === 'function') {
                    e.preventDefault();
                }
                await submitPaymentClaim();
            });
        }

        function syncInitialActiveOrderState() {
            const activeOrder = parseActiveOrderData(documentRef);
            if (!activeOrder) return;
            if (activeOrder.already_claimed) {
                renderConfirmedState(activeOrder, DEFAULT_CLAIM_CONFIRMATION_MSG);
            } else if (elements.amountInput && activeOrder.amount) {
                elements.amountInput.value = activeOrder.amount;
            }
        }

        function openTopupOrTermsModal() {
            if (
                modalEl.dataset.termsRequired === 'true'
                && modalEl.dataset.termsAccepted !== 'true'
            ) {
                const openTermsFn = getOpenTermsFn();
                if (openTermsFn) {
                    openTermsFn(function () {
                        modalEl.dataset.termsAccepted = 'true';
                        toggleModal(modalEl, windowRef, 'show');
                    });
                    return;
                }
            }
            toggleModal(modalEl, windowRef, 'show');
        }

        if (elements.navbarTopupBtn) {
            elements.navbarTopupBtn.addEventListener('click', function (e) {
                if (e && typeof e.preventDefault === 'function') {
                    e.preventDefault();
                }
                openTopupOrTermsModal();
            });
        }

        syncInitialActiveOrderState();

        modalEl.addEventListener('show.bs.modal', function (e) {
            if (
                modalEl.dataset.termsRequired === 'true'
                && modalEl.dataset.termsAccepted !== 'true'
            ) {
                const openTermsFn = getOpenTermsFn();
                if (openTermsFn) {
                    if (e && typeof e.preventDefault === 'function') {
                        e.preventDefault();
                    }
                    openTermsFn(function () {
                        modalEl.dataset.termsAccepted = 'true';
                        toggleModal(modalEl, windowRef, 'show');
                    });
                    return;
                }
            }
            setAlert(elements.errorAlert, '');
            syncInitialActiveOrderState();
        });

        if (hasTopupQueryParam(windowRef)) {
            openTopupOrTermsModal();
        }

        const refundController = initRefundControls(documentRef);

        return {
            syncBep20Gate: syncBep20Gate,
            submitInitiateOrder: submitInitiateOrder,
            submitPaymentClaim: submitPaymentClaim,
            submitRefundRequest: refundController ? refundController.submitRefundRequest : null,
            submitPayoutCancelForm: refundController ? refundController.submitPayoutCancelForm : null,
        };
    }

    function initRefundControls(documentRef) {
        const refundModal = documentRef.getElementById('refundModal');
        const payoutsContainer = documentRef.getElementById('studentPayoutsContainer');
        if (!refundModal && !payoutsContainer) return null;

        const refundErrorAlert = documentRef.getElementById('refundErrorAlert');
        const refundStepForm = documentRef.getElementById('refundStepForm');
        const refundStepSubmitted = documentRef.getElementById('refundStepSubmitted');
        const refundRequestForm = documentRef.getElementById('refundRequestForm');
        const refundAmountInput = documentRef.getElementById('refundAmountInput');
        const refundDestinationInput = documentRef.getElementById('refundDestinationInput');
        const refundNotesInput = documentRef.getElementById('refundNotesInput');
        const submitRefundRequestBtn = documentRef.getElementById('submitRefundRequestBtn');
        const refundConfirmationNotice = documentRef.getElementById('refundConfirmationNotice');
        const refundSubmittedMeta = documentRef.getElementById('refundSubmittedMeta');
        const payoutsAlert = documentRef.getElementById('studentPayoutsAlert');
        const availValueEl = documentRef.getElementById('walletAvailableBalanceValue');
        const reservedValueEl = documentRef.getElementById('walletReservedBalanceValue');
        const settledValueEl = documentRef.getElementById('walletSettledBalanceValue');

        function showRefundStep(stepName) {
            if (refundStepForm) {
                refundStepForm.classList.toggle('d-none', stepName !== 'form');
            }
            if (refundStepSubmitted) {
                refundStepSubmitted.classList.toggle('d-none', stepName !== 'submitted');
            }
        }

        async function submitRefundRequest() {
            if (!refundModal || !refundRequestForm) return;
            if (refundModal.dataset.isFrozen === 'true') {
                setAlert(
                    refundErrorAlert,
                    'Your wallet is currently frozen. New refund requests are paused.',
                    'alert-danger'
                );
                return;
            }

            const rawAmount = refundAmountInput ? refundAmountInput.value.trim() : '';
            const parsedAmount = Number.parseFloat(rawAmount);
            const available = Number.parseFloat(refundModal.dataset.availableBalance || '0.00');

            if (!rawAmount || Number.isNaN(parsedAmount) || parsedAmount <= 0) {
                setAlert(refundErrorAlert, 'Refund amount must be greater than $0.00.', 'alert-danger');
                return;
            }
            if (parsedAmount > available) {
                setAlert(
                    refundErrorAlert,
                    `Refund amount cannot exceed your available balance ($${available.toFixed(2)}).`,
                    'alert-danger'
                );
                return;
            }

            const destination = refundDestinationInput ? refundDestinationInput.value.trim() : '';
            if (!destination) {
                setAlert(refundErrorAlert, 'Destination payment details are required.', 'alert-danger');
                return;
            }

            const notes = refundNotesInput ? refundNotesInput.value.trim() : '';
            const requestUrl = refundModal.dataset.requestUrl || refundRequestForm.getAttribute('action') || '';
            if (!requestUrl) return;

            const body =
                `amount=${encodeURIComponent(rawAmount)}`
                + `&destination=${encodeURIComponent(destination)}`
                + `&notes=${encodeURIComponent(notes)}`;

            const result = await postWalletForm(
                documentRef,
                requestUrl,
                body,
                submitRefundRequestBtn,
                'Sending...',
                'Send Confirmation Email',
                refundErrorAlert
            );
            if (result.networkError) return;

            if (!result.ok) {
                const msg = (result.data && result.data.error)
                    ? result.data.error
                    : 'Unable to submit your refund request. Please try again.';
                setAlert(refundErrorAlert, msg, 'alert-danger');
                return;
            }

            setAlert(refundErrorAlert, '');
            if (refundConfirmationNotice && result.data.message) {
                refundConfirmationNotice.textContent = result.data.message;
            }
            if (refundSubmittedMeta) {
                refundSubmittedMeta.textContent =
                    `Request #${result.data.payout_id} · $${result.data.amount_gross} ${result.data.currency || 'USD'}`;
            }
            if (payoutsContainer && typeof result.data.payouts_html === 'string') {
                payoutsContainer.innerHTML = result.data.payouts_html;
            }
            if (refundAmountInput) refundAmountInput.value = '';
            if (refundNotesInput) refundNotesInput.value = '';
            showRefundStep('submitted');
        }

        async function submitPayoutCancelForm(formEl) {
            if (!formEl) return;
            const actionUrl = (typeof formEl.getAttribute === 'function' ? formEl.getAttribute('action') : '') || formEl.action || '';
            if (!actionUrl) return;
            const submitBtn = typeof formEl.querySelector === 'function'
                ? formEl.querySelector('button[type="submit"]')
                : null;

            const result = await postWalletForm(
                documentRef,
                actionUrl,
                '',
                submitBtn,
                'Cancelling...',
                'Cancel',
                payoutsAlert
            );
            if (result.networkError) return;

            if (!result.ok) {
                const msg = (result.data && result.data.error)
                    ? result.data.error
                    : 'Unable to cancel this refund request.';
                setAlert(payoutsAlert, msg, 'alert-danger');
                return;
            }

            setAlert(payoutsAlert, result.data.message || 'Refund request cancelled.', 'alert-success');
            if (payoutsContainer && typeof result.data.payouts_html === 'string') {
                payoutsContainer.innerHTML = result.data.payouts_html;
            }
            if (availValueEl && result.data.available_balance !== undefined) {
                availValueEl.textContent = `$${result.data.available_balance}`;
            }
            if (reservedValueEl && result.data.reserved_balance !== undefined) {
                reservedValueEl.textContent = `$${result.data.reserved_balance}`;
            }
            if (settledValueEl && result.data.settled_balance !== undefined) {
                settledValueEl.textContent = `$${result.data.settled_balance}`;
            }
            if (refundModal && result.data.available_balance !== undefined) {
                refundModal.dataset.availableBalance = result.data.available_balance;
            }
        }

        if (refundModal && refundModal.dataset.refundModalBound !== 'true') {
            refundModal.dataset.refundModalBound = 'true';
            refundModal.addEventListener('show.bs.modal', function () {
                setAlert(refundErrorAlert, '');
                showRefundStep('form');
            });
            if (refundRequestForm) {
                refundRequestForm.addEventListener('submit', async function (e) {
                    if (e && typeof e.preventDefault === 'function') {
                        e.preventDefault();
                    }
                    await submitRefundRequest();
                });
            }
        }

        if (payoutsContainer && payoutsContainer.dataset.payoutCancelBound !== 'true') {
            payoutsContainer.dataset.payoutCancelBound = 'true';
            payoutsContainer.addEventListener('submit', async function (e) {
                const target = e && e.target;
                if (
                    target
                    && target.classList
                    && target.classList.contains('student-payout-cancel-form')
                ) {
                    if (typeof e.preventDefault === 'function') {
                        e.preventDefault();
                    }
                    await submitPayoutCancelForm(target);
                }
            });
        }

        return {
            submitRefundRequest: submitRefundRequest,
            submitPayoutCancelForm: submitPayoutCancelForm,
        };
    }

    return {
        initStudentWallet: initStudentWallet,
    };
}));
