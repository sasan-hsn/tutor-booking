

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

    const teacherName = dayPicker.dataset.teacherName;
    const teacherInitial = dayPicker.dataset.teacherInitial;

    document.addEventListener('click', (e) => {
        const slotBtn = e.target.closest('.slot-open');
        if (!slotBtn) return;

        document.getElementById('modalTeacherAvatar').textContent = teacherInitial;
        document.getElementById('modalTeacherName').textContent = teacherName;
        // it should change dynamically based on the slot type (issue #23), but for now we can just set it to "Regular Lesson"
        document.getElementById('modalLessonTypeBadge').textContent = 'Regular Lesson';
        document.getElementById('modalLessonDate').textContent = slotBtn.dataset.weekday;
        document.getElementById('modalLessonTime').textContent =
            `${slotBtn.dataset.start} - ${slotBtn.dataset.end}`;

        document.getElementById('confirmBookingBtn').dataset.slotId = slotBtn.dataset.slotId;

        modal.show();
    });
}

document.addEventListener('DOMContentLoaded', initBookingModal);