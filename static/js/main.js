

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

function csrfFetch(url, options = {}) {
    const csrftoken = getCookie('csrftoken');
    options.headers = {
        ...options.headers,
        'X-CSRFToken': csrftoken,
    };
    return fetch(url, options);
}


function initLessonCardsScroll() {
    const scrollEl = document.getElementById('lessonCardsScroll');
    if (!scrollEl) return;

    const wrapper = scrollEl.closest('.lesson-cards-wrapper');
    const leftArrow = wrapper.querySelector('.scroll-arrow-left');
    const rightArrow = wrapper.querySelector('.scroll-arrow-right');
    const scrollAmount = 320; // roughly one card width + gap

    function updateArrows() {
        leftArrow.classList.toggle('is-hidden', scrollEl.scrollLeft <= 0);
        rightArrow.classList.toggle(
            'is-hidden',
            scrollEl.scrollLeft + scrollEl.clientWidth >= scrollEl.scrollWidth - 1
        );
    }

    leftArrow.addEventListener('click', () => {
        scrollEl.scrollBy({ left: -scrollAmount, behavior: 'smooth' });
    });

    rightArrow.addEventListener('click', () => {
        scrollEl.scrollBy({ left: scrollAmount, behavior: 'smooth' });
    });

    scrollEl.addEventListener('scroll', updateArrows);
    window.addEventListener('resize', updateArrows);
    scrollEl.addEventListener('wheel', (e) => {
        if (e.deltaY === 0) return;
        e.preventDefault();
        scrollEl.scrollBy({ left: e.deltaY, behavior: 'smooth' });
    });
    updateArrows();
}


document.addEventListener('DOMContentLoaded', initLessonCardsScroll);


function initWeekNav() {
    const dayPicker = document.getElementById('dayPicker');
    if (!dayPicker) return;

    const prevBtn = document.getElementById('weekPrevBtn');
    const nextBtn = document.getElementById('weekNextBtn');
    const label = document.getElementById('weekRangeLabel');

    async function loadWeek(weekStart) {
        const url = `${window.bookingWeekAjaxUrl}?week_start=${weekStart}`;
        const response = await fetch(url);
        if (!response.ok) return;
        const data = await response.json();

        dayPicker.innerHTML = data.html;
        label.textContent = data.week_label;

        prevBtn.dataset.weekStart = data.prev_week_start;
        prevBtn.disabled = !data.can_go_prev;

        nextBtn.dataset.weekStart = data.next_week_start;
    }

    prevBtn.addEventListener('click', () => loadWeek(prevBtn.dataset.weekStart));
    nextBtn.addEventListener('click', () => loadWeek(nextBtn.dataset.weekStart));
}

document.addEventListener('DOMContentLoaded', initWeekNav);



function initBookingModal() {
    const dayPicker = document.getElementById('dayPicker');
    if (!dayPicker) return;

    const modalEl = document.getElementById('confirmBookingModal');
    const modal = new bootstrap.Modal(modalEl);
    const confirmBtn = document.getElementById('confirmBookingBtn');

    const teacherName = dayPicker.dataset.teacherName;
    const teacherInitial = dayPicker.dataset.teacherInitial;

    document.addEventListener('click', (e) => {
        const slotBtn = e.target.closest('.slot-open');
        if (!slotBtn) return;

        document.getElementById('modalTeacherAvatar').textContent = teacherInitial;
        document.getElementById('modalTeacherName').textContent = teacherName;
        document.getElementById('modalLessonDate').textContent = slotBtn.dataset.weekday;
        document.getElementById('modalLessonTime').textContent =
            `${slotBtn.dataset.start} - ${slotBtn.dataset.end}`;

        confirmBtn.dataset.slotId = slotBtn.dataset.slotId;
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Next';

        modal.show();
    });

    confirmBtn.addEventListener('click', async () => {
        const slotId = confirmBtn.dataset.slotId;
        if (!slotId) return;

        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Booking...';

        try {
            const response = await csrfFetch(window.bookSlotUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `slot_id=${encodeURIComponent(slotId)}`,
            });

            const data = await response.json();

            if (!response.ok) {
                alert(data.error || 'Something went wrong. Please try again.');
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Next';
                return;
            }

            modal.hide();
            window.location.reload();

        } catch (err) {
            alert('Network error. Please try again.');
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Next';
        }
    });
}

document.addEventListener('DOMContentLoaded', initBookingModal);



function initLessonRequestsModal() {
    const modalEl = document.getElementById('lessonRequestsModal');
    if (!modalEl) return;

    const modalBody = document.getElementById('lessonRequestsModalBody');
    const url = modalEl.dataset.url;

    modalEl.addEventListener('show.bs.modal', function () {
        modalBody.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
        fetch(url)
            .then(response => response.text())
            .then(html => { modalBody.innerHTML = html; })
            .catch(() => {
                modalBody.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
            });
    });
}

document.addEventListener('DOMContentLoaded', initLessonRequestsModal);


function initLessonRequestsActions() {
    const modalEl = document.getElementById('lessonRequestsModal');
    if (!modalEl) return;

    const modalBody = document.getElementById('lessonRequestsModalBody');
    const respondUrlTemplate = modalEl.dataset.respondUrl;

    let hasChanges = false;

    modalBody.addEventListener('click', async (e) => {
        const btn = e.target.closest('.btn-accept, .btn-decline');
        if (!btn) return;

        const isDecline = btn.classList.contains('btn-decline');
        if (isDecline && !confirm('Are you sure you want to decline this lesson request?')) {
            return;
        }

        const bookingId = btn.dataset.bookingId;
        const action = isDecline ? 'decline' : 'accept';
        const item = btn.closest('.request-item');
        const url = respondUrlTemplate.replace('/0/', `/${bookingId}/`);

        btn.disabled = true;

        try {
            const response = await csrfFetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `action=${action}`,
            });

            if (!response.ok) {
                alert('Something went wrong. Please try again.');
                btn.disabled = false;
                return;
            }

            item.remove();
            hasChanges = true;
            const badge = document.querySelector('#openLessonRequestsBtn .badge-count');
            if (badge) {
                const newCount = parseInt(badge.textContent, 10) - 1;
                if (newCount > 0) {
                    badge.textContent = newCount;
                } else {
                    badge.remove();
                }
            }

        } catch (err) {
            alert('Network error. Please try again.');
            btn.disabled = false;
        }
    });

    modalEl.addEventListener('hidden.bs.modal', () => {
        if (hasChanges) {
            window.location.reload();
        }
    });
}

document.addEventListener('DOMContentLoaded', initLessonRequestsActions);