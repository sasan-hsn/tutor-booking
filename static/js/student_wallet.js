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

    function setAlert(alertEl, message) {
        if (!alertEl) return;
        alertEl.textContent = message || '';
        if (message) {
            alertEl.classList.remove('d-none');
        } else {
            alertEl.classList.add('d-none');
        }
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
            bep20Checkbox: documentRef.getElementById('bep20AckCheckbox'),
            lockedHint: documentRef.getElementById('topupDepositLockedHint'),
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

            if (elements.depositDetails) {
                elements.depositDetails.classList.toggle('d-none', !unlocked);
            }
            if (elements.lockedHint) {
                elements.lockedHint.classList.toggle('d-none', unlocked);
            }
            if (elements.addressInput) {
                elements.addressInput.value = unlocked ? state.currentOrder.address : '';
            }
            if (elements.qrImg) {
                elements.qrImg.src = unlocked ? (state.currentOrder.qr_data_uri || '') : '';
            }
            if (elements.copyBtn) {
                elements.copyBtn.disabled = !unlocked;
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
            if (elements.amountInput && orderData.amount) {
                elements.amountInput.value = orderData.amount;
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

        async function postTopupForm(url, body, buttonEl, busyLabel, defaultLabel) {
            setAlert(elements.errorAlert, '');
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
                        'X-CSRFToken': getCsrfToken(documentRef),
                    },
                    body: body,
                });
                try {
                    data = await response.json();
                } catch (_) {
                    data = null;
                }
            } catch (_) {
                setAlert(elements.errorAlert, 'Network error. Please try again.');
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
            if (!state.currentOrder || !state.currentOrder.claim_url) return;

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

        return {
            syncBep20Gate: syncBep20Gate,
            submitInitiateOrder: submitInitiateOrder,
            submitPaymentClaim: submitPaymentClaim,
        };
    }

    return {
        initStudentWallet: initStudentWallet,
    };
}));
