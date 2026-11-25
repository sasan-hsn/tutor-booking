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

    function showFinanceToast(message, containerEl, doc) {
        const documentRef = doc || (typeof document !== 'undefined' ? document : null);
        if (!containerEl || !documentRef) return null;

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
        icon.className = 'bi bi-check-circle-fill text-success fs-5';
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

    function initTeacherFinance(doc) {
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
            financeOverviewContainer: documentRef.getElementById('financeOverviewContainer'),
            financeClaimsContainer: documentRef.getElementById('financeClaimsContainer'),
            financeLedgerContainer: documentRef.getElementById('financeLedgerContainer'),
            notificationToastContainer: documentRef.getElementById('notificationToastContainer'),
        };

        if (elements.openManualTopupBtn) {
            elements.openManualTopupBtn.addEventListener('click', function () {
                resetManualTopupForm(elements);
            });
        }

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

        if (elements.manualTopupForm) {
            elements.manualTopupForm.addEventListener('submit', async function (e) {
                if (e && typeof e.preventDefault === 'function') {
                    e.preventDefault();
                }

                clearErrorAlert(elements.manualTopupErrorAlert);
                if (elements.manualTopupIdempotencyKey && !elements.manualTopupIdempotencyKey.value) {
                    elements.manualTopupIdempotencyKey.value = generateIdempotencyToken();
                }

                const submitBtn = elements.manualTopupSubmitBtn;
                const originalLabel = submitBtn ? submitBtn.textContent : '';
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Processing...';
                }

                try {
                    const formData = new FormData(elements.manualTopupForm);
                    const response = await fetch(elements.manualTopupForm.action, {
                        method: 'POST',
                        headers: {
                            'X-Requested-With': 'XMLHttpRequest',
                            'X-CSRFToken': getCsrfToken(documentRef),
                        },
                        body: formData,
                    });

                    let data = null;
                    try {
                        data = await response.json();
                    } catch (_) {
                        data = null;
                    }

                    if (!response.ok || !data || !data.success) {
                        const errMsg = (data && data.error)
                            ? data.error
                            : 'Unable to process manual top-up. Please check your inputs.';
                        showErrorAlert(elements.manualTopupErrorAlert, errMsg);
                        return;
                    }

                    if (elements.financeOverviewContainer && typeof data.overview_html === 'string') {
                        elements.financeOverviewContainer.innerHTML = data.overview_html;
                    }
                    if (elements.financeClaimsContainer && typeof data.claims_html === 'string') {
                        elements.financeClaimsContainer.innerHTML = data.claims_html;
                    }
                    if (elements.financeLedgerContainer && typeof data.ledger_html === 'string') {
                        elements.financeLedgerContainer.innerHTML = data.ledger_html;
                    }

                    toggleModal(elements.manualTopupModal, 'hide');
                    resetManualTopupForm(elements);
                    showFinanceToast(
                        data.message || 'Manual top-up credited successfully.',
                        elements.notificationToastContainer,
                        documentRef
                    );
                } catch (_) {
                    showErrorAlert(
                        elements.manualTopupErrorAlert,
                        'Network error. Please try again.'
                    );
                } finally {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = originalLabel || 'Credit Student Wallet';
                    }
                }
            });
        }
    }

    return {
        generateIdempotencyToken: generateIdempotencyToken,
        prefillManualTopupForm: prefillManualTopupForm,
        resetManualTopupForm: resetManualTopupForm,
        showFinanceToast: showFinanceToast,
        initTeacherFinance: initTeacherFinance,
    };
}));
