

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

        confirmBtn.dataset.date = slotBtn.dataset.date;
        confirmBtn.dataset.start = slotBtn.dataset.start;
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Next';

        modal.show();
    });

    confirmBtn.addEventListener('click', async () => {
        const dateVal = confirmBtn.dataset.date;
        const startVal = confirmBtn.dataset.start;
        if (!dateVal || !startVal) return;

        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Booking...';

        try {
            const response = await csrfFetch(window.bookSlotUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `date=${encodeURIComponent(dateVal)}&start_time=${encodeURIComponent(startVal)}`,
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



const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function isHourActive(hour, ranges) {
    if (!ranges || ranges.length === 0) return false;
    return ranges.some(range => {
        const [startH, startM] = range.start.split(':').map(Number);
        const [endH, endM] = range.end.split(':').map(Number);
        const start = startH + startM / 60;
        const end = endH + endM / 60;
        return hour >= start && hour < end;
    });
}

function generateTimeColumnHtml() {
    let html = '<div class="timetable-time-col"><div class="timetable-time-header"></div>';
    for (let hour = 0; hour < 24; hour++) {
        const label = (hour % 6 === 0) ? `${String(hour).padStart(2, '0')}:00` : '';
        html += `<div class="timetable-time-slot">${label}</div>`;
    }
    html += '</div>';
    return html;
}

function renderRegularScheduleGrid(data) {
    const grid = document.getElementById('regularScheduleGrid');
    let html = '<div class="timetable-container">';

    html += generateTimeColumnHtml();
    html += '<div class="schedule-grid">';

    for (let day = 0; day < 7; day++) {
        const ranges = data[String(day)] || [];
        html += `<div class="schedule-day-column" data-day="${day}">`;
        html += `<div class="schedule-day-label">${DAY_LABELS[day]}</div>`;

        for (let hour = 0; hour < 24; hour++) {
            const active = isHourActive(hour, ranges);
            const activeClass = active ? ' is-available' : '';
            const hStart = String(hour).padStart(2, '0') + ':00';
            const hEnd = String((hour + 1) % 24).padStart(2, '0') + ':00';
            const timeLabel = `${hStart} - ${hEnd}`;

            html += `<div class="schedule-hour-cell${activeClass}" data-time="${timeLabel}"></div>`;
        }

        html += '</div>';
    }

    html += '</div></div>';
    grid.innerHTML = html;
}

function generateTimeOptions() {
    let options = '';
    for (let hour = 0; hour < 24; hour++) {
        for (let minute of [0, 30]) {
            const h = String(hour).padStart(2, '0');
            const m = String(minute).padStart(2, '0');
            const value = `${h}:${m}`;
            options += `<option value="${value}">${value}</option>`;
        }
    }
    return options;
}

/* ===== Regular Schedule Modal ===== */
function initScheduleModal() {
    const modalEl = document.getElementById('scheduleModal');
    if (!modalEl) return;

    document.getElementById('dayEditStartSelect').innerHTML = generateTimeOptions();
    document.getElementById('dayEditEndSelect').innerHTML = generateTimeOptions();

    const url = modalEl.dataset.url;
    const grid = document.getElementById('regularScheduleGrid');
    const dayEditPanel = document.getElementById('dayEditPanel');
    const dayEditTitle = document.getElementById('dayEditTitle');
    const dayEditExistingRanges = document.getElementById('dayEditExistingRanges');
    const backBtn = document.getElementById('dayEditBackBtn');
    const addUrl = modalEl.dataset.addUrl;
    const addBtn = document.getElementById('dayEditAddBtn');
    const startSelect = document.getElementById('dayEditStartSelect');
    const endSelect = document.getElementById('dayEditEndSelect');
    const deleteUrlTemplate = modalEl.dataset.deleteUrl;

    let scheduleData = {};
    let currentDay = null;

    modalEl.addEventListener('show.bs.modal', function () {
        grid.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
        dayEditPanel.classList.add('d-none');
        grid.classList.remove('d-none');

        fetch(url)
            .then(response => response.json())
            .then(data => {
                scheduleData = data;
                renderRegularScheduleGrid(data);
            })
            .catch(() => {
                grid.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
            });
    });

    grid.addEventListener('click', (e) => {
        const column = e.target.closest('.schedule-day-column');
        if (!column) return;

        currentDay = column.dataset.day;
        openDayEditPanel(currentDay);
    });

    backBtn.addEventListener('click', () => {
        dayEditPanel.classList.add('d-none');
        grid.classList.remove('d-none');
    });

    addBtn.addEventListener('click', async () => {
        const start = startSelect.value;
        const end = endSelect.value;

        addBtn.disabled = true;

        try {
            const response = await csrfFetch(addUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `day_of_week=${currentDay}&start_time=${start}&end_time=${end}`,
            });

            const data = await response.json();

            if (!response.ok) {
                alert(data.error ? JSON.stringify(data.error) : 'Something went wrong.');
                addBtn.disabled = false;
                return;
            }

            if (!scheduleData[currentDay]) {
                scheduleData[currentDay] = [];
            }
            scheduleData[currentDay].push({ id: data.id, start: data.start, end: data.end });

            renderExistingRanges(currentDay);
            renderRegularScheduleGrid(scheduleData);

        } catch (err) {
            alert('Network error. Please try again.');
        } finally {
            addBtn.disabled = false;
        }
    });

    dayEditExistingRanges.addEventListener('click', async (e) => {
        const btn = e.target.closest('.remove-range-btn');
        if (!btn) return;

        if (!confirm('Remove this availability?')) return;

        const availabilityId = btn.dataset.id;
        const url = deleteUrlTemplate.replace('/0/', `/${availabilityId}/`);

        btn.disabled = true;

        try {
            const response = await csrfFetch(url, { method: 'POST' });

            if (!response.ok) {
                alert('Something went wrong. Please try again.');
                btn.disabled = false;
                return;
            }

            scheduleData[currentDay] = scheduleData[currentDay].filter(r => r.id != availabilityId);
            renderExistingRanges(currentDay);
            renderRegularScheduleGrid(scheduleData);

        } catch (err) {
            alert('Network error. Please try again.');
            btn.disabled = false;
        }
    });

    function openDayEditPanel(day) {
        dayEditTitle.textContent = DAY_LABELS[day];
        renderExistingRanges(day);

        grid.classList.add('d-none');
        dayEditPanel.classList.remove('d-none');
    }

    function renderExistingRanges(day) {
        const ranges = scheduleData[day] || [];

        if (ranges.length === 0) {
            dayEditExistingRanges.innerHTML = '<p class="text-muted small">No availability set for this day.</p>';
            return;
        }

        let html = '';
        ranges.forEach(range => {
            html += `
                <div class="existing-range" data-id="${range.id}">
                    <span>${range.start}–${range.end}</span>
                    <button type="button" class="remove-range-btn" data-id="${range.id}">⊗</button>
                </div>
            `;
        });
        dayEditExistingRanges.innerHTML = html;
    }
}

document.addEventListener('DOMContentLoaded', initScheduleModal);



function renderOverrideScheduleGrid(data) {
    const grid = document.getElementById('overrideScheduleGrid');
    let html = '<div class="timetable-container">';

    html += generateTimeColumnHtml();
    html += '<div class="schedule-grid">';

    Object.keys(data).sort().forEach(dateStr => {
        const dayData = data[dateStr];
        const dateObj = new Date(dateStr + 'T00:00:00');
        const label = dateObj.toLocaleDateString('en-US', { weekday: 'short', day: 'numeric' });
        const disabledClass = dayData.is_past ? ' schedule-day-disabled' : '';

        html += `<div class="schedule-day-column${disabledClass}" data-date="${dateStr}">`;
        html += `<div class="schedule-day-label">${label}</div>`;

        const hasOverrides = dayData.override_ranges && dayData.override_ranges.length > 0;

        for (let hour = 0; hour < 24; hour++) {
            let cellClass = '';

            if (hasOverrides) {
                if (isHourActive(hour, dayData.override_ranges)) {
                    cellClass = ' is-override';
                }
            } else {
                if (isHourActive(hour, dayData.regular_ranges)) {
                    cellClass = ' is-available';
                }
            }

            const hStart = String(hour).padStart(2, '0') + ':00';
            const hEnd = String((hour + 1) % 24).padStart(2, '0') + ':00';
            const timeLabel = `${hStart} - ${hEnd}`;

            html += `<div class="schedule-hour-cell${cellClass}" data-time="${timeLabel}"></div>`;
        }

        html += '</div>';
    });

    html += '</div></div>';
    grid.innerHTML = html;
}


/* ===== Weekly Override Modal ===== */
function initOverrideScheduleModal() {
    const modalEl = document.getElementById('scheduleModal');
    if (!modalEl) return;

    document.getElementById('overrideDayEditStartSelect').innerHTML = generateTimeOptions();
    document.getElementById('overrideDayEditEndSelect').innerHTML = generateTimeOptions();

    const url = modalEl.dataset.overrideUrl;
    const addUrl = modalEl.dataset.overrideAddUrl;
    const deleteUrlTemplate = modalEl.dataset.overrideDeleteUrl;

    const grid = document.getElementById('overrideScheduleGrid');
    const dayEditPanel = document.getElementById('overrideDayEditPanel');
    const dayEditTitle = document.getElementById('overrideDayEditTitle');
    const dayEditExistingRanges = document.getElementById('overrideDayEditExistingRanges');
    const backBtn = document.getElementById('overrideDayEditBackBtn');
    const addBtn = document.getElementById('overrideDayEditAddBtn');
    const startSelect = document.getElementById('overrideDayEditStartSelect');
    const endSelect = document.getElementById('overrideDayEditEndSelect');

    let scheduleData = {};
    let currentDate = null;

    modalEl.addEventListener('show.bs.modal', function () {
        grid.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
        dayEditPanel.classList.add('d-none');
        grid.classList.remove('d-none');

        fetch(url)
            .then(response => response.json())
            .then(data => {
                scheduleData = data;
                renderOverrideScheduleGrid(data);
            })
            .catch(() => {
                grid.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
            });
    });

    grid.addEventListener('click', (e) => {
        const column = e.target.closest('.schedule-day-column');
        if (!column || column.classList.contains('schedule-day-disabled')) return;

        currentDate = column.dataset.date;
        openDayEditPanel(currentDate);
    });

    backBtn.addEventListener('click', () => {
        dayEditPanel.classList.add('d-none');
        grid.classList.remove('d-none');
    });

    addBtn.addEventListener('click', async () => {
        const start = startSelect.value;
        const end = endSelect.value;

        addBtn.disabled = true;

        try {
            const response = await csrfFetch(addUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `date=${currentDate}&start_time=${start}&end_time=${end}`,
            });

            const data = await response.json();

            if (!response.ok) {
                alert(data.error ? JSON.stringify(data.error) : 'Something went wrong.');
                addBtn.disabled = false;
                return;
            }

            scheduleData[currentDate].override_ranges.push({ id: data.id, start: data.start, end: data.end });

            renderExistingOverrideRanges(currentDate);
            renderOverrideScheduleGrid(scheduleData);

        } catch (err) {
            alert('Network error. Please try again.');
        } finally {
            addBtn.disabled = false;
        }
    });

    dayEditExistingRanges.addEventListener('click', async (e) => {
        const btn = e.target.closest('.remove-range-btn');
        if (!btn) return;

        if (!confirm('Remove this availability?')) return;

        const overrideId = btn.dataset.id;
        const delUrl = deleteUrlTemplate.replace('/0/', `/${overrideId}/`);

        btn.disabled = true;

        try {
            const response = await csrfFetch(delUrl, { method: 'POST' });

            if (!response.ok) {
                alert('Something went wrong. Please try again.');
                btn.disabled = false;
                return;
            }

            scheduleData[currentDate].override_ranges = scheduleData[currentDate].override_ranges.filter(r => r.id != overrideId);
            renderExistingOverrideRanges(currentDate);
            renderOverrideScheduleGrid(scheduleData);

        } catch (err) {
            alert('Network error. Please try again.');
            btn.disabled = false;
        }
    });

    function openDayEditPanel(dateStr) {
        const dateObj = new Date(dateStr + 'T00:00:00');
        dayEditTitle.textContent = dateObj.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
        renderExistingOverrideRanges(dateStr);

        grid.classList.add('d-none');
        dayEditPanel.classList.remove('d-none');
    }

    function renderExistingOverrideRanges(dateStr) {
        const dayData = scheduleData[dateStr];
        let html = '';
        const hasOverrides = dayData.override_ranges && dayData.override_ranges.length > 0;

        if (hasOverrides) {
            html += '<p class="text-muted small mb-1"><strong>This week\'s schedule (Overrides regular schedule):</strong></p>';
            dayData.override_ranges.forEach(range => {
                html += `
                    <div class="existing-range" data-id="${range.id}">
                        <span>${range.start}–${range.end}</span>
                        <button type="button" class="remove-range-btn" data-id="${range.id}">⊗</button>
                    </div>
                `;
            });
            html += '<p class="text-muted small mt-2"><em>Regular schedule for this day is overridden.</em></p>';
        } else {
            if (dayData.regular_ranges && dayData.regular_ranges.length > 0) {
                html += '<p class="text-muted small mb-1">Regular schedule (Active):</p>';
                dayData.regular_ranges.forEach(range => {
                    html += `<div class="existing-range existing-range-regular"><span>${range.start}–${range.end}</span></div>`;
                });
                html += '<p class="text-muted small mt-2"><em>Adding an override will replace all regular slots for this day.</em></p>';
            } else {
                html = '<p class="text-muted small">No availability set for this day.</p>';
            }
        }

        dayEditExistingRanges.innerHTML = html;
    }
}

document.addEventListener('DOMContentLoaded', initOverrideScheduleModal);


/* ==========================================================================
   Teacher Calendar (#30, #31)
   ========================================================================== */

document.addEventListener("DOMContentLoaded", function () {
    const calendarPage = document.querySelector(".calendar-page");
    if (!calendarPage) return;

    const gridContainer = document.getElementById("calendar-grid-container");
    const monthLabel = document.querySelector(".week-range-label");

    const MAX_VISIBLE_PER_DAY = 3;

    /* ---------- Overflow ("+N more") ---------- */
    function updateOverflow() {
        document.querySelectorAll(".calendar-day").forEach((day) => {
            const allRows = Array.from(day.querySelectorAll(".calendar-booking-row"));
            const visibleRows = allRows.filter((row) => !row.classList.contains("filter-hidden"));

            visibleRows.forEach((row, index) => {
                const overflow = index >= MAX_VISIBLE_PER_DAY;
                row.style.display = overflow ? "none" : "";
            });

            allRows.forEach((row) => {
                if (row.classList.contains("filter-hidden")) {
                    row.style.display = "none";
                }
            });

            const moreBtn = day.querySelector(".calendar-more-link");
            if (!moreBtn) return;

            const hiddenCount = visibleRows.length - MAX_VISIBLE_PER_DAY;
            if (hiddenCount > 0) {
                moreBtn.textContent = `+${hiddenCount} more`;
                moreBtn.style.display = "block";
            } else {
                moreBtn.style.display = "none";
            }
        });
    }

    /* ---------- Dropdown open/close (Display / Lessons with) ---------- */
    function setupDropdown(buttonId, panelId) {
        const btn = document.getElementById(buttonId);
        const panel = document.getElementById(panelId);
        if (!btn || !panel) return;

        btn.addEventListener("click", function (e) {
            e.stopPropagation();
            panel.classList.toggle("open");
        });

        document.addEventListener("click", function (e) {
            if (!panel.contains(e.target) && !btn.contains(e.target)) {
                panel.classList.remove("open");
            }
        });
    }

    setupDropdown("status-filter-btn", "status-filter-panel");
    setupDropdown("student-filter-btn", "student-filter-panel");

    /* ---------- Client-side filtering ---------- */
    function applyFilters() {
        const checkedStatuses = Array.from(
            document.querySelectorAll(".status-checkbox:checked")
        ).map((cb) => cb.value);

        const checkedStudents = Array.from(
            document.querySelectorAll(".student-checkbox:checked")
        ).map((cb) => cb.value);

        document.querySelectorAll(".calendar-booking-row").forEach((row) => {
            const status = row.dataset.status;
            const studentId = row.dataset.studentId;

            const statusOk = checkedStatuses.includes(status);
            const studentOk =
                checkedStudents.length === 0 || checkedStudents.includes(studentId);

            row.classList.toggle("filter-hidden", !(statusOk && studentOk));
        });

        updateOverflow();
    }

    document.addEventListener("change", function (e) {
        if (e.target.matches(".status-checkbox") || e.target.matches(".student-checkbox")) {
            applyFilters();
        }
    });

    /* ---------- Student search (inside "Lessons with" dropdown) ---------- */
    const studentSearch = document.getElementById("student-filter-search");
    if (studentSearch) {
        studentSearch.addEventListener("input", function () {
            const query = studentSearch.value.trim().toLowerCase();
            document.querySelectorAll(".student-filter-option").forEach((option) => {
                const name = option.textContent.trim().toLowerCase();
                option.style.display = name.includes(query) ? "" : "none";
            });
        });
    }

    /* ---------- Month navigation (AJAX) ---------- */
    function loadMonth(url) {
        fetch(url)
            .then((response) => response.text())
            .then((html) => {
                gridContainer.innerHTML = html;

                const newGrid = gridContainer.querySelector(".calendar-grid");
                if (newGrid && monthLabel) {
                    monthLabel.textContent = newGrid.dataset.monthLabel;
                }

                applyFilters();
            });
    }

    document.addEventListener("click", function (e) {
        const navBtn = e.target.closest(".week-nav-arrow");
        if (navBtn) {
            loadMonth(navBtn.dataset.url);
        }
    });

    /* ---------- "+N more" day popup ---------- */
    const dayMorePopup = document.getElementById("dayMorePopup");
    const dayMorePopupHeader = document.getElementById("dayMorePopupHeader");
    const dayMorePopupList = document.getElementById("dayMorePopupList");

    function openDayMorePopup(btn, dayCell) {
        const visibleRows = Array.from(
            dayCell.querySelectorAll(".calendar-booking-row")
        ).filter((row) => !row.classList.contains("filter-hidden"));

        dayMorePopupHeader.textContent = dayCell.querySelector(".calendar-day-number").textContent;

        dayMorePopupList.innerHTML = "";
        visibleRows.forEach((row) => {
            const clone = row.cloneNode(true);
            clone.style.display = "";
            dayMorePopupList.appendChild(clone);
        });

        const rect = dayCell.getBoundingClientRect();
        dayMorePopup.style.top = `${rect.top}px`;
        dayMorePopup.style.left = `${rect.left}px`;
        dayMorePopup.style.width = `${rect.width}px`;
        dayMorePopup.style.minHeight = `${rect.height}px`;
        dayMorePopup.classList.add("open");
    }

    document.addEventListener("click", function (e) {
        const moreBtn = e.target.closest(".calendar-more-link");
        if (moreBtn) {
            e.stopPropagation();
            const dayCell = moreBtn.closest(".calendar-day");
            openDayMorePopup(moreBtn, dayCell);
            return;
        }

        if (!dayMorePopup.contains(e.target)) {
            dayMorePopup.classList.remove("open");
        }
    });

    /* ---------- Lesson detail modal (#31) ---------- */
    function initLessonDetailModal() {
        const modalEl = document.getElementById("lessonDetailModal");
        if (!modalEl) return;

        const modalBody = document.getElementById("lessonDetailModalBody");
        const urlTemplate = modalEl.dataset.url;
        const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);

        document.addEventListener("click", function (e) {
            const row = e.target.closest(".calendar-booking-row");
            if (!row) return;

            const bookingId = row.dataset.bookingId;
            const url = urlTemplate.replace("/0/", `/${bookingId}/`);

            modalBody.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
            modalInstance.show();

            fetch(url)
                .then((response) => response.text())
                .then((html) => {
                    modalBody.innerHTML = html;
                })
                .catch(() => {
                    modalBody.innerHTML =
                        '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
                });
        });
    }

    function initCancelLesson() {
        const modalBody = document.getElementById("lessonDetailModalBody");
        if (!modalBody) return;

        let hasChanges = false;

        modalBody.addEventListener("click", async function (e) {
            const btn = e.target.closest("#cancelLessonBtn");
            if (!btn) return;

            if (!confirm("Are you sure you want to cancel this lesson?")) return;

            const url = btn.dataset.cancelUrl;

            btn.disabled = true;

            try {
                const response = await csrfFetch(url, { method: "POST" });

                if (!response.ok) {
                    alert("Something went wrong. Please try again.");
                    btn.disabled = false;
                    return;
                }

                hasChanges = true;
                const modalEl = document.getElementById("lessonDetailModal");
                bootstrap.Modal.getInstance(modalEl).hide();
            } catch (err) {
                alert("Network error. Please try again.");
                btn.disabled = false;
            }
        });

        const modalEl = document.getElementById("lessonDetailModal");
        modalEl.addEventListener("hidden.bs.modal", function () {
            if (hasChanges) {
                window.location.reload();
            }
        });
    }

    function initRequestCancellation() {
        const modalBody = document.getElementById("lessonDetailModalBody");
        if (!modalBody) return;

        let hasChanges = false;

        modalBody.addEventListener("click", async function (e) {
            const btn = e.target.closest("#requestCancellationBtn");
            if (!btn) return;

            if (!confirm("Request cancellation for this lesson? Your teacher will need to approve it.")) return;

            const url = btn.dataset.requestUrl;

            btn.disabled = true;

            try {
                const response = await csrfFetch(url, { method: "POST" });

                if (!response.ok) {
                    alert("Something went wrong. Please try again.");
                    btn.disabled = false;
                    return;
                }

                hasChanges = true;
                const modalEl = document.getElementById("lessonDetailModal");
                bootstrap.Modal.getInstance(modalEl).hide();
            } catch (err) {
                alert("Network error. Please try again.");
                btn.disabled = false;
            }
        });

        const modalEl = document.getElementById("lessonDetailModal");
        modalEl.addEventListener("hidden.bs.modal", function () {
            if (hasChanges) {
                window.location.reload();
            }
        });
    }

    /* ---------- Init ---------- */
    applyFilters();
    initLessonDetailModal();
    initCancelLesson();
    initRequestCancellation();
});