/**
 * teacher_finance.js - Teacher Finance tab controller for manual top-up modal,
 * client-side UUID idempotency token generation, claim pre-fill, and AJAX refresh.
 */

(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.TeacherFinance = factory();
    }
}(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    function getCsrfToken(doc) {
        const documentRef = doc || (typeof document !== 'undefined' ? document : null);
        if (!documentRef || !documentRef.cookie) return '';
        const cookies = documentRef.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, 10) === 'csrftoken=') {
                return decodeURIComponent(cookie.substring(10));
            }
        }
        return '';
    }

    function generateIdempotencyToken() {
        if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
            return crypto.randomUUID();
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            const r = (Math.random() * 16) | 0;
            const v = c === 'x' ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    }

    function clearErrorAlert(errorAlertEl) {
        if (!errorAlertEl) return;
        errorAlertEl.textContent = '';
        errorAlertEl.classList.add('d-none');
    }

    function showErrorAlert(errorAlertEl, message) {
        if (!errorAlertEl) return;
        errorAlertEl.textContent = message;
        errorAlertEl.classList.remove('d-none');
    }

    function setFormValues(elements, values) {
        if (elements.manualTopupStudent) elements.manualTopupStudent.value = String(values.studentId || '');
        if (elements.manualTopupAmount) elements.manualTopupAmount.value = String(values.amount || '');
        if (elements.manualTopupReference) elements.manualTopupReference.value = String(values.reference || '');
        if (elements.manualTopupNotes) elements.manualTopupNotes.value = String(values.notes || '');
        if (elements.manualTopupIdempotencyKey) {
            elements.manualTopupIdempotencyKey.value = generateIdempotencyToken();
        }
        clearErrorAlert(elements.manualTopupErrorAlert);
    }

    function resetManualTopupForm(elements) {
        setFormValues(elements, {});
    }

    function prefillManualTopupForm(claimData, elements) {
        setFormValues(elements, claimData || {});
    }

    function toggleModal(modalEl, action) {
        if (
            modalEl
            && typeof window !== 'undefined'
            && window.bootstrap
            && window.bootstrap.Modal
        ) {
            const modalInstance = window.bootstrap.Modal.getOrCreateInstance(modalEl);
            if (typeof modalInstance[action] === 'function') {
                modalInstance[action]();
            }
        }
    }

    function showFinanceToast(message, containerEl, doc, options) {
        const documentRef = doc || (typeof document !== 'undefined' ? document : null);
        if (!containerEl || !documentRef) return null;

        const isError = Boolean(options && options.isError);
        const toastEl = documentRef.createElement('div');
        toastEl.className = 'toast align-items-center border-0 shadow notification-toast';
        toastEl.setAttribute('role', 'alert');
        toastEl.setAttribute('aria-live', 'assertive');
        toastEl.setAttribute('aria-atomic', 'true');

        const flexRow = documentRef.createElement('div');
        flexRow.className = 'd-flex';

        const toastBody = documentRef.createElement('div');
        toastBody.className = 'toast-body d-flex gap-2 align-items-center flex-grow-1';

        const icon = documentRef.createElement('i');
        icon.className = isError
            ? 'bi bi-exclamation-triangle-fill text-danger fs-5'
            : 'bi bi-check-circle-fill text-success fs-5';
        toastBody.appendChild(icon);

        const textEl = documentRef.createElement('span');
        textEl.className = 'small fw-medium text-dark';
        textEl.textContent = message;
        toastBody.appendChild(textEl);

        const closeBtn = documentRef.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'btn-close me-2 m-auto';
        closeBtn.setAttribute('data-bs-dismiss', 'toast');
        closeBtn.setAttribute('aria-label', 'Close');

        flexRow.appendChild(toastBody);
        flexRow.appendChild(closeBtn);
        toastEl.appendChild(flexRow);
        containerEl.appendChild(toastEl);

        if (typeof window !== 'undefined' && window.bootstrap && typeof window.bootstrap.Toast === 'function') {
            const toast = new window.bootstrap.Toast(toastEl, { delay: 5000, autohide: true });
            toast.show();
            toastEl.addEventListener('hidden.bs.toast', function () {
                toastEl.remove();
            });
        }

        return toastEl;
    }

    async function copyTextToClipboard(copyBtn) {
        const text = copyBtn && copyBtn.dataset ? copyBtn.dataset.copyText : '';
        if (!text) return false;

        const clipboard = (typeof navigator !== 'undefined' && navigator.clipboard)
            ? navigator.clipboard
            : null;
        if (!clipboard || typeof clipboard.writeText !== 'function') {
            return false;
        }

        try {
            await clipboard.writeText(text);
        } catch (_) {
            return false;
        }

        const icon = typeof copyBtn.querySelector === 'function' ? copyBtn.querySelector('i') : null;
        if (icon) {
            if (copyBtn._copyFeedbackTimer) {
                clearTimeout(copyBtn._copyFeedbackTimer);
            }
            icon.className = 'bi bi-check2 text-success';
            copyBtn._copyFeedbackTimer = setTimeout(function () {
                icon.className = 'bi bi-clipboard';
                copyBtn._copyFeedbackTimer = null;
            }, 1500);
        }

        return true;
    }

    function formatRemainingCountdown(deadlineIso, nowMs, expiredLabel) {
        if (!deadlineIso) return '—';
        const deadlineMs = Date.parse(deadlineIso);
        if (Number.isNaN(deadlineMs)) return '—';
        const currentMs = typeof nowMs === 'number' ? nowMs : Date.now();
        const remainingSeconds = Math.floor((deadlineMs - currentMs) / 1000);
        if (remainingSeconds <= 0) return expiredLabel;
        const days = Math.floor(remainingSeconds / 86400);
        const hours = Math.floor((remainingSeconds % 86400) / 3600);
        const minutes = Math.floor((remainingSeconds % 3600) / 60);
        if (days > 0) return `${days}d ${hours}h left`;
        if (hours > 0) return `${hours}h ${minutes}m left`;
        return `${Math.max(minutes, 1)}m left`;
    }

    function formatDisputeCountdown(deadlineIso, nowMs) {
        return formatRemainingCountdown(deadlineIso, nowMs, 'Auto-expiring');
    }

    function formatAddressCountdown(activatesAtIso, nowMs) {
        return formatRemainingCountdown(activatesAtIso, nowMs, 'Activating soon');
    }

    function updateCountdownBadges(containerEl, selector, dataKey, formatterFn, nowMs) {
        if (!containerEl || typeof containerEl.querySelectorAll !== 'function') return;
        const badges = containerEl.querySelectorAll(selector);
        for (let i = 0; i < badges.length; i++) {
            const badge = badges[i];
            const isoValue = badge.dataset ? badge.dataset[dataKey] : '';
            badge.textContent = formatterFn(isoValue, nowMs);
        }
    }

    function updateDisputeCountdowns(containerEl, nowMs) {
        updateCountdownBadges(
            containerEl,
            '[data-dispute-deadline]',
            'disputeDeadline',
            formatDisputeCountdown,
            nowMs
        );
    }

    function updateAddressCountdowns(containerEl, nowMs) {
        updateCountdownBadges(
            containerEl,
            '[data-address-activates-at]',
            'addressActivatesAt',
            formatAddressCountdown,
            nowMs
        );
    }

    function applyFinanceHtmlUpdates(elements, data) {
        if (!data) return;
        if (elements.financeOverviewContainer && typeof data.overview_html === 'string') {
            elements.financeOverviewContainer.innerHTML = data.overview_html;
        }
        if (elements.financeClaimsContainer && typeof data.claims_html === 'string') {
            elements.financeClaimsContainer.innerHTML = data.claims_html;
        }
        if (elements.financePayoutsContainer && typeof data.payouts_html === 'string') {
            elements.financePayoutsContainer.innerHTML = data.payouts_html;
        }
        if (elements.financeDisputesContainer && typeof data.disputes_html === 'string') {
            elements.financeDisputesContainer.innerHTML = data.disputes_html;
            updateDisputeCountdowns(elements.financeDisputesContainer);
        }
        if (elements.financeLedgerContainer && typeof data.ledger_html === 'string') {
            elements.financeLedgerContainer.innerHTML = data.ledger_html;
        }
        if (
            elements.financeReceivingAddressContainer
            && typeof data.receiving_address_html === 'string'
        ) {
            elements.financeReceivingAddressContainer.innerHTML = data.receiving_address_html;
            updateAddressCountdowns(elements.financeReceivingAddressContainer);
        }
    }

    async function postFinanceRequest(url, documentRef, body) {
        const fetchOptions = {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': getCsrfToken(documentRef),
            },
        };
        if (body !== undefined) {
            fetchOptions.body = body;
        }
        const response = await fetch(url, fetchOptions);
        let data = null;
        try {
            data = await response.json();
        } catch (_) {
            data = null;
        }
        return { ok: response.ok && Boolean(data && data.success), data: data };
    }

    function bindFinanceFormSubmit(options) {
        const {
            form,
            modal,
            errorAlert,
            submitBtn,
            defaultLabel,
            fallbackError,
            defaultSuccessMessage,
            elements,
            documentRef,
            onSuccess,
            onError,
        } = options;
        if (!form) return;

        form.addEventListener('submit', async function (e) {
            if (e && typeof e.preventDefault === 'function') {
                e.preventDefault();
            }

            clearErrorAlert(errorAlert);
            if (typeof options.beforeSubmit === 'function') {
                options.beforeSubmit();
            }

            const originalLabel = submitBtn ? submitBtn.textContent : '';
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Processing...';
            }

            try {
                const formData = new FormData(form);
                const result = await postFinanceRequest(form.action, documentRef, formData);
                if (!result.ok) {
                    const errMsg = (result.data && result.data.error) ? result.data.error : fallbackError;
                    showErrorAlert(errorAlert, errMsg);
                    if (typeof onError === 'function') {
                        onError(result.data);
                    }
                    return;
                }

                applyFinanceHtmlUpdates(elements, result.data);
                toggleModal(modal, 'hide');
                if (typeof onSuccess === 'function') {
                    onSuccess(result.data);
                }
                showFinanceToast(
                    result.data.message || defaultSuccessMessage,
                    elements.notificationToastContainer,
                    documentRef
                );
            } catch (_) {
                showErrorAlert(errorAlert, 'Network error. Please try again.');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalLabel || defaultLabel;
                }
            }
        });
    }

    function openPayoutActionModal(options) {
        const {
            triggerBtn,
            form,
            summaryEl,
            inputEl,
            errorAlert,
            modal,
            verb,
        } = options;
        if (!triggerBtn || !form) return;

        form.action = triggerBtn.dataset.actionUrl || '';
        if (summaryEl) {
            const studentName = triggerBtn.dataset.studentName || 'Student';
            const amount = triggerBtn.dataset.amount || '0.00';
            const payoutId = triggerBtn.dataset.payoutId || '';
            summaryEl.textContent = `${verb} payout #${payoutId} ($${amount}) for ${studentName}.`;
        }
        if (inputEl) {
            inputEl.value = '';
        }
        clearErrorAlert(errorAlert);
        toggleModal(modal, 'show');
    }

    function prefillClaimByIdFromQuery(elements, win) {
        const windowRef = win || (typeof window !== 'undefined' ? window : null);
        const search = (windowRef && windowRef.location && windowRef.location.search) || '';
        if (!search || !elements.financeClaimsContainer || typeof elements.financeClaimsContainer.querySelector !== 'function') {
            return;
        }
        const params = new URLSearchParams(search);
        const rawClaimId = (params.get('claim_id') || '').trim();
        if (!/^\d+$/.test(rawClaimId)) return;

        const claimBtn = elements.financeClaimsContainer.querySelector(
            `.btn-credit-deposit[data-order-id="${rawClaimId}"]`
        );
        if (!claimBtn || !claimBtn.dataset) return;

        prefillManualTopupForm(
            {
                studentId: claimBtn.dataset.studentId,
                amount: claimBtn.dataset.amount,
                reference: claimBtn.dataset.reference,
                notes: claimBtn.dataset.notes,
            },
            elements
        );
        toggleModal(elements.manualTopupModal, 'show');
    }

    function initTeacherFinance(doc, win) {
        const documentRef = doc || (typeof document !== 'undefined' ? document : null);
        if (!documentRef) return;

        const elements = {
            openManualTopupBtn: documentRef.getElementById('openManualTopupBtn'),
            manualTopupModal: documentRef.getElementById('manualTopupModal'),
            manualTopupForm: documentRef.getElementById('manualTopupForm'),
            manualTopupStudent: documentRef.getElementById('manualTopupStudent'),
            manualTopupAmount: documentRef.getElementById('manualTopupAmount'),
            manualTopupReference: documentRef.getElementById('manualTopupReference'),
            manualTopupNotes: documentRef.getElementById('manualTopupNotes'),
            manualTopupIdempotencyKey: documentRef.getElementById('manualTopupIdempotencyKey'),
            manualTopupErrorAlert: documentRef.getElementById('manualTopupErrorAlert'),
            manualTopupSubmitBtn: documentRef.getElementById('manualTopupSubmitBtn'),
            completePayoutModal: documentRef.getElementById('completePayoutModal'),
            completePayoutForm: documentRef.getElementById('completePayoutForm'),
            completePayoutSummary: documentRef.getElementById('completePayoutSummary'),
            completePayoutTxhash: documentRef.getElementById('completePayoutTxhash'),
            completePayoutErrorAlert: documentRef.getElementById('completePayoutErrorAlert'),
            completePayoutSubmitBtn: documentRef.getElementById('completePayoutSubmitBtn'),
            rejectPayoutModal: documentRef.getElementById('rejectPayoutModal'),
            rejectPayoutForm: documentRef.getElementById('rejectPayoutForm'),
            rejectPayoutSummary: documentRef.getElementById('rejectPayoutSummary'),
            rejectPayoutReason: documentRef.getElementById('rejectPayoutReason'),
            rejectPayoutErrorAlert: documentRef.getElementById('rejectPayoutErrorAlert'),
            rejectPayoutSubmitBtn: documentRef.getElementById('rejectPayoutSubmitBtn'),
            resolveDisputeModal: documentRef.getElementById('resolveDisputeModal'),
            resolveDisputeForm: documentRef.getElementById('resolveDisputeForm'),
            resolveDisputeSummary: documentRef.getElementById('resolveDisputeSummary'),
            resolveDisputeConsequence: documentRef.getElementById('resolveDisputeConsequence'),
            resolveDisputeResolution: documentRef.getElementById('resolveDisputeResolution'),
            resolveDisputeErrorAlert: documentRef.getElementById('resolveDisputeErrorAlert'),
            resolveDisputeSubmitBtn: documentRef.getElementById('resolveDisputeSubmitBtn'),
            financeOverviewContainer: documentRef.getElementById('financeOverviewContainer'),
            financeClaimsContainer: documentRef.getElementById('financeClaimsContainer'),
            financePayoutsContainer: documentRef.getElementById('financePayoutsContainer'),
            financeDisputesContainer: documentRef.getElementById('financeDisputesContainer'),
            financeLedgerContainer: documentRef.getElementById('financeLedgerContainer'),
            financeReceivingAddressContainer: documentRef.getElementById('financeReceivingAddressContainer'),
            receivingAddressForm: documentRef.getElementById('receivingAddressForm'),
            receivingAddressInput: documentRef.getElementById('receivingAddressInput'),
            receivingAddressPassword: documentRef.getElementById('receivingAddressPassword'),
            receivingAddressErrorAlert: documentRef.getElementById('receivingAddressErrorAlert'),
            receivingAddressSubmitBtn: documentRef.getElementById('receivingAddressSubmitBtn'),
            notificationToastContainer: documentRef.getElementById('notificationToastContainer'),
        };

        updateDisputeCountdowns(elements.financeDisputesContainer);
        updateAddressCountdowns(elements.financeReceivingAddressContainer);
        if (
            (
                elements.financeDisputesContainer
                && typeof elements.financeDisputesContainer.querySelectorAll === 'function'
                && elements.financeDisputesContainer.querySelectorAll('[data-dispute-deadline]').length > 0
            )
            || (
                elements.financeReceivingAddressContainer
                && typeof elements.financeReceivingAddressContainer.querySelectorAll === 'function'
                && elements.financeReceivingAddressContainer.querySelectorAll('[data-address-activates-at]').length > 0
            )
        ) {
            setInterval(function () {
                updateDisputeCountdowns(elements.financeDisputesContainer);
                updateAddressCountdowns(elements.financeReceivingAddressContainer);
            }, 60000);
        }

        if (elements.openManualTopupBtn) {
            elements.openManualTopupBtn.addEventListener('click', function () {
                resetManualTopupForm(elements);
            });
        }

        prefillClaimByIdFromQuery(elements, win);

        if (elements.financeClaimsContainer) {
            elements.financeClaimsContainer.addEventListener('click', function (e) {
                const claimBtn = e.target && typeof e.target.closest === 'function'
                    ? e.target.closest('.btn-credit-deposit')
                    : null;
                if (!claimBtn) return;

                prefillManualTopupForm(
                    {
                        studentId: claimBtn.dataset.studentId,
                        amount: claimBtn.dataset.amount,
                        reference: claimBtn.dataset.reference,
                        notes: claimBtn.dataset.notes,
                    },
                    elements
                );
                toggleModal(elements.manualTopupModal, 'show');
            });
        }

        if (elements.financePayoutsContainer) {
            elements.financePayoutsContainer.addEventListener('click', async function (e) {
                const target = e.target && typeof e.target.closest === 'function' ? e.target : null;
                if (!target) return;

                const processingBtn = target.closest('.btn-mark-payout-processing');
                if (processingBtn) {
                    const actionUrl = processingBtn.dataset ? processingBtn.dataset.actionUrl : '';
                    if (!actionUrl) return;

                    processingBtn.disabled = true;
                    try {
                        const result = await postFinanceRequest(actionUrl, documentRef);
                        if (result.ok) {
                            applyFinanceHtmlUpdates(elements, result.data);
                            showFinanceToast(
                                result.data.message || 'Payout marked as processing.',
                                elements.notificationToastContainer,
                                documentRef
                            );
                        } else {
                            const errMsg = (result.data && result.data.error)
                                ? result.data.error
                                : 'Unable to mark payout as processing.';
                            showFinanceToast(
                                errMsg,
                                elements.notificationToastContainer,
                                documentRef,
                                { isError: true }
                            );
                        }
                    } catch (_) {
                        showFinanceToast(
                            'Network error. Please try again.',
                            elements.notificationToastContainer,
                            documentRef,
                            { isError: true }
                        );
                    } finally {
                        processingBtn.disabled = false;
                    }
                    return;
                }

                const completeBtn = target.closest('.btn-complete-payout');
                if (completeBtn) {
                    openPayoutActionModal({
                        triggerBtn: completeBtn,
                        form: elements.completePayoutForm,
                        summaryEl: elements.completePayoutSummary,
                        inputEl: elements.completePayoutTxhash,
                        errorAlert: elements.completePayoutErrorAlert,
                        modal: elements.completePayoutModal,
                        verb: 'Complete',
                    });
                    return;
                }

                const rejectBtn = target.closest('.btn-reject-payout');
                if (rejectBtn) {
                    openPayoutActionModal({
                        triggerBtn: rejectBtn,
                        form: elements.rejectPayoutForm,
                        summaryEl: elements.rejectPayoutSummary,
                        inputEl: elements.rejectPayoutReason,
                        errorAlert: elements.rejectPayoutErrorAlert,
                        modal: elements.rejectPayoutModal,
                        verb: 'Reject',
                    });
                }
            });
        }

        if (elements.financeDisputesContainer) {
            elements.financeDisputesContainer.addEventListener('click', function (e) {
                const target = e.target && typeof e.target.closest === 'function' ? e.target : null;
                if (!target) return;

                const resolveBtn = target.closest('.btn-resolve-dispute');
                if (!resolveBtn || !elements.resolveDisputeForm) return;

                const resolution = resolveBtn.dataset.resolution || '';
                const studentName = resolveBtn.dataset.studentName || 'Student';
                const amount = resolveBtn.dataset.amount || '0.00';
                const bookingId = resolveBtn.dataset.bookingId || '';
                const isStudentFavor = resolution === 'STUDENT_FAVOR';

                elements.resolveDisputeForm.action = resolveBtn.dataset.actionUrl || '';
                if (elements.resolveDisputeResolution) {
                    elements.resolveDisputeResolution.value = resolution;
                }
                if (elements.resolveDisputeSummary) {
                    const favorLabel = isStudentFavor ? 'Student Favor' : 'Teacher Favor';
                    elements.resolveDisputeSummary.textContent = (
                        `Resolve dispute for Booking #${bookingId} (${studentName}, $${amount}) in ${favorLabel}?`
                    );
                }
                if (elements.resolveDisputeConsequence) {
                    elements.resolveDisputeConsequence.textContent = isStudentFavor
                        ? `Held funds ($${amount}) will be released back to ${studentName}'s available wallet balance.`
                        : `Held funds ($${amount}) will be consumed as completed lesson revenue.`;
                }
                if (elements.resolveDisputeSubmitBtn) {
                    elements.resolveDisputeSubmitBtn.className = isStudentFavor
                        ? 'btn btn-danger'
                        : 'btn btn-primary';
                    elements.resolveDisputeSubmitBtn.textContent = isStudentFavor
                        ? 'Confirm Student Favor'
                        : 'Confirm Teacher Favor';
                }
                clearErrorAlert(elements.resolveDisputeErrorAlert);
                toggleModal(elements.resolveDisputeModal, 'show');
            });
        }

        if (typeof documentRef.addEventListener === 'function') {
            documentRef.addEventListener('click', function (e) {
                const copyBtn = e.target && typeof e.target.closest === 'function'
                    ? e.target.closest('[data-copy-text]')
                    : null;
                if (!copyBtn) return;
                copyTextToClipboard(copyBtn);
            });
        }

        bindFinanceFormSubmit({
            form: elements.manualTopupForm,
            modal: elements.manualTopupModal,
            errorAlert: elements.manualTopupErrorAlert,
            submitBtn: elements.manualTopupSubmitBtn,
            defaultLabel: 'Credit Student Wallet',
            fallbackError: 'Unable to process manual top-up. Please check your inputs.',
            defaultSuccessMessage: 'Manual top-up credited successfully.',
            elements: elements,
            documentRef: documentRef,
            beforeSubmit: function () {
                if (elements.manualTopupIdempotencyKey && !elements.manualTopupIdempotencyKey.value) {
                    elements.manualTopupIdempotencyKey.value = generateIdempotencyToken();
                }
            },
            onSuccess: function () {
                resetManualTopupForm(elements);
            },
        });

        bindFinanceFormSubmit({
            form: elements.completePayoutForm,
            modal: elements.completePayoutModal,
            errorAlert: elements.completePayoutErrorAlert,
            submitBtn: elements.completePayoutSubmitBtn,
            defaultLabel: 'Confirm & Complete Payout',
            fallbackError: 'Unable to complete payout. Please check the transaction hash.',
            defaultSuccessMessage: 'Payout completed successfully.',
            elements: elements,
            documentRef: documentRef,
            onSuccess: function () {
                if (elements.completePayoutTxhash) elements.completePayoutTxhash.value = '';
            },
        });

        bindFinanceFormSubmit({
            form: elements.rejectPayoutForm,
            modal: elements.rejectPayoutModal,
            errorAlert: elements.rejectPayoutErrorAlert,
            submitBtn: elements.rejectPayoutSubmitBtn,
            defaultLabel: 'Reject & Release Funds',
            fallbackError: 'Unable to reject payout. Please provide a rejection reason.',
            defaultSuccessMessage: 'Payout rejected and funds released.',
            elements: elements,
            documentRef: documentRef,
            onSuccess: function () {
                if (elements.rejectPayoutReason) elements.rejectPayoutReason.value = '';
            },
        });

        bindFinanceFormSubmit({
            form: elements.resolveDisputeForm,
            modal: elements.resolveDisputeModal,
            errorAlert: elements.resolveDisputeErrorAlert,
            submitBtn: elements.resolveDisputeSubmitBtn,
            defaultLabel: 'Confirm Resolution',
            fallbackError: 'Unable to resolve this dispute. Please try again.',
            defaultSuccessMessage: 'Dispute resolved successfully.',
            elements: elements,
            documentRef: documentRef,
        });

        bindFinanceFormSubmit({
            form: elements.receivingAddressForm,
            modal: null,
            errorAlert: elements.receivingAddressErrorAlert,
            submitBtn: elements.receivingAddressSubmitBtn,
            defaultLabel: 'Save Address',
            fallbackError: 'Unable to update receiving address. Please check your inputs.',
            defaultSuccessMessage: 'Receiving address updated.',
            elements: elements,
            documentRef: documentRef,
            onSuccess: function () {
                if (elements.receivingAddressInput) elements.receivingAddressInput.value = '';
                if (elements.receivingAddressPassword) elements.receivingAddressPassword.value = '';
            },
            onError: function () {
                if (elements.receivingAddressPassword) elements.receivingAddressPassword.value = '';
            },
        });
    }

    return {
        generateIdempotencyToken: generateIdempotencyToken,
        prefillManualTopupForm: prefillManualTopupForm,
        resetManualTopupForm: resetManualTopupForm,
        showFinanceToast: showFinanceToast,
        copyTextToClipboard: copyTextToClipboard,
        formatDisputeCountdown: formatDisputeCountdown,
        formatAddressCountdown: formatAddressCountdown,
        initTeacherFinance: initTeacherFinance,
    };
}));
