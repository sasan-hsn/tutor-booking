

/* --------------------------------------------------------------------------
   1. CSRF & Network Utilities
   -------------------------------------------------------------------------- */
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

/* --------------------------------------------------------------------------
   2. Auth & Timezone Utilities
   -------------------------------------------------------------------------- */
function initSignupTimezoneDetection() {
    const tzSelect = document.querySelector('#signupForm select[name="timezone"]');
    if (!tzSelect) return;

    const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const hasOption = [...tzSelect.options].some(opt => opt.value === detected);
    if (hasOption) {
        tzSelect.value = detected;
    }
}

/* --------------------------------------------------------------------------
   3. UI Components (Horizontal Scroll & Carousels)
   -------------------------------------------------------------------------- */
function initLessonCardsScroll() {
    const scrollEls = document.querySelectorAll('.lesson-cards-scroll');
    if (scrollEls.length === 0) return;

    scrollEls.forEach((scrollEl) => {
        const wrapper = scrollEl.closest('.lesson-cards-wrapper');
        if (!wrapper) return;

        const leftArrow = wrapper.querySelector('.scroll-arrow-left');
        const rightArrow = wrapper.querySelector('.scroll-arrow-right');
        const scrollAmount = 320;

        function updateArrows() {
            if (leftArrow) {
                leftArrow.classList.toggle('is-hidden', scrollEl.scrollLeft <= 0);
            }
            if (rightArrow) {
                rightArrow.classList.toggle(
                    'is-hidden',
                    scrollEl.scrollLeft + scrollEl.clientWidth >= scrollEl.scrollWidth - 1
                );
            }
        }

        if (leftArrow) {
            leftArrow.addEventListener('click', () => {
                scrollEl.scrollBy({ left: -scrollAmount, behavior: 'smooth' });
            });
        }

        if (rightArrow) {
            rightArrow.addEventListener('click', () => {
                scrollEl.scrollBy({ left: scrollAmount, behavior: 'smooth' });
            });
        }

        scrollEl.addEventListener('scroll', updateArrows);
        window.addEventListener('resize', updateArrows);
        scrollEl.addEventListener('wheel', (e) => {
            if (e.deltaY === 0) return;
            e.preventDefault();
            scrollEl.scrollBy({ left: e.deltaY, behavior: 'smooth' });
        });

        updateArrows();
    });
}

/* --------------------------------------------------------------------------
   4. Student Booking Flow
   -------------------------------------------------------------------------- */
function initWeekNav() {
    const dayPicker = document.getElementById('dayPicker');
    if (!dayPicker) return;

    const prevBtn = document.getElementById('weekPrevBtn');
    const nextBtn = document.getElementById('weekNextBtn');
    const label = document.getElementById('weekRangeLabel');
    const weekAjaxUrl = dayPicker.dataset.weekAjaxUrl;

    async function loadWeek(weekStart) {
        const url = `${weekAjaxUrl}?week_start=${weekStart}`;
        try {
            const response = await fetch(url);
            if (!response.ok) return;
            const data = await response.json();

            dayPicker.innerHTML = data.html;
            label.textContent = data.week_label;

            prevBtn.dataset.weekStart = data.prev_week_start;
            prevBtn.disabled = !data.can_go_prev;

            nextBtn.dataset.weekStart = data.next_week_start;
        } catch (err) {
            console.error('Error loading week slots:', err);
        }
    }

    if (prevBtn) prevBtn.addEventListener('click', () => loadWeek(prevBtn.dataset.weekStart));
    if (nextBtn) nextBtn.addEventListener('click', () => loadWeek(nextBtn.dataset.weekStart));
}

function initBookingModal() {
    const dayPicker = document.getElementById('dayPicker');
    const modalEl = document.getElementById('confirmBookingModal');
    if (!dayPicker || !modalEl) return;

    const modal = new bootstrap.Modal(modalEl);
    const confirmBtn = document.getElementById('confirmBookingBtn');

    const teacherName = dayPicker.dataset.teacherName;
    const teacherInitial = dayPicker.dataset.teacherInitial;
    const teacherAvatarUrl = dayPicker.dataset.teacherAvatarUrl;
    const teacherAvatarColor = dayPicker.dataset.teacherAvatarColor;
    const lessonTypeDisplay = dayPicker.dataset.lessonTypeDisplay;
    const bookSlotUrl = dayPicker.dataset.bookSlotUrl;

    document.addEventListener('click', (e) => {
        const slotBtn = e.target.closest('.slot-open');
        if (!slotBtn) return;

        const avatarEl = document.getElementById('modalTeacherAvatar');
        if (avatarEl) {
            if (teacherAvatarUrl) {
                avatarEl.outerHTML = `<img src="${teacherAvatarUrl}" alt="Avatar" class="rounded-circle" id="modalTeacherAvatar" style="width: 40px; height: 40px; object-fit: cover;">`;
            } else {
                avatarEl.outerHTML = `<div class="rounded-circle d-flex align-items-center justify-content-center text-white" id="modalTeacherAvatar" style="width: 40px; height: 40px; background-color: ${teacherAvatarColor}; font-weight: 600;">${teacherInitial}</div>`;
            }
        }

        document.getElementById('modalTeacherName').textContent = teacherName;
        document.getElementById('modalLessonTypeBadge').textContent = lessonTypeDisplay;
        document.getElementById('modalLessonDate').textContent = slotBtn.dataset.weekday;
        document.getElementById('modalLessonTime').textContent = `${slotBtn.dataset.start} - ${slotBtn.dataset.end}`;

        confirmBtn.dataset.startAt = slotBtn.dataset.startAt;
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Next';

        modal.show();
    });

    confirmBtn.addEventListener('click', async () => {
        const startAtVal = confirmBtn.dataset.startAt;
        if (!startAtVal) return;

        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Booking...';

        try {
            const response = await csrfFetch(bookSlotUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `start_at=${encodeURIComponent(startAtVal)}`,
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

/* --------------------------------------------------------------------------
   5. Lesson Detail Modals & Actions
   -------------------------------------------------------------------------- */
function initLessonDetailModalManager() {
    const modalEl = document.getElementById('lessonDetailModal');
    if (!modalEl) return;

    const modalBody = document.getElementById('lessonDetailModalBody');
    const urlTemplate = modalEl.dataset.url;
    const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
    let hasChanges = false;

    // Open detail from lesson card (Dashboard)
    document.addEventListener('click', (e) => {
        const card = e.target.closest('.lesson-card');
        if (!card) return;

        const bookingId = card.dataset.bookingId;
        const url = urlTemplate.replace('/0/', `/${bookingId}/`);

        modalBody.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
        modalInstance.show();

        fetch(url)
            .then(res => res.text())
            .then(html => { modalBody.innerHTML = html; })
            .catch(() => {
                modalBody.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
            });
    });

    // Delegate Modal Actions (Cancel, Complete, Not Held, Request Cancellation)
    modalBody.addEventListener('click', async (e) => {
        // 1. Cancel lesson (Teacher direct)
        const cancelBtn = e.target.closest('#cancelLessonBtn');
        if (cancelBtn) {
            if (!confirm('Are you sure you want to cancel this lesson?')) return;
            await handleModalPostAction(cancelBtn, cancelBtn.dataset.cancelUrl);
            return;
        }

        // 2. Request cancellation (Student)
        const reqCancelBtn = e.target.closest('#requestCancellationBtn');
        if (reqCancelBtn) {
            if (!confirm('Request cancellation for this lesson? Your teacher will need to approve it.')) return;
            await handleModalPostAction(reqCancelBtn, reqCancelBtn.dataset.requestUrl);
            return;
        }

        // 3. Complete lesson (Teacher)
        const completeBtn = e.target.closest('#completeLessonBtn');
        if (completeBtn) {
            const noteInput = document.getElementById('completionNoteInput');
            const note = noteInput ? noteInput.value : '';
            if (!confirm('Mark this lesson as completed?')) return;
            await handleModalPostAction(completeBtn, completeBtn.dataset.completeUrl, {
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: `note=${encodeURIComponent(note)}`
            });
            return;
        }

        // 4. Mark not held (Teacher)
        const notHeldBtn = e.target.closest('#markNotHeldBtn');
        if (notHeldBtn) {
            if (!confirm('Mark this lesson as not held? This will cancel the booking.')) return;
            await handleModalPostAction(notHeldBtn, notHeldBtn.dataset.notHeldUrl);
            return;
        }
    });

    async function handleModalPostAction(btn, url, extraOptions = {}) {
        btn.disabled = true;
        try {
            const response = await csrfFetch(url, { method: 'POST', ...extraOptions });
            if (!response.ok) {
                alert('Something went wrong. Please try again.');
                btn.disabled = false;
                return;
            }
            hasChanges = true;
            modalInstance.hide();
        } catch (err) {
            alert('Network error. Please try again.');
            btn.disabled = false;
        }
    }

    // Student Review Form Submit
    modalBody.addEventListener('submit', async (e) => {
        const form = e.target.closest('#reviewForm');
        if (!form) return;
        e.preventDefault();

        const submitBtn = form.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Submitting...';

        try {
            const response = await csrfFetch(form.action, {
                method: 'POST',
                body: new FormData(form),
            });
            const data = await response.json();

            if (!response.ok) {
                alert(data.error || 'Something went wrong. Please try again.');
                submitBtn.disabled = false;
                submitBtn.textContent = 'Submit Review';
                return;
            }

            modalInstance.hide();
            window.location.reload();
        } catch (err) {
            alert('Network error. Please try again.');
            submitBtn.disabled = false;
            submitBtn.textContent = 'Submit Review';
        }
    });

    // Auto-reload on dismissal if changes occurred
    modalEl.addEventListener('hidden.bs.modal', () => {
        if (hasChanges) window.location.reload();
    });
}

/* --------------------------------------------------------------------------
   6. Teacher Requests Modal & Badges
   -------------------------------------------------------------------------- */
function initLessonRequestsManager() {
    const modalEl = document.getElementById('lessonRequestsModal');
    if (!modalEl) return;

    const modalBody = document.getElementById('lessonRequestsModalBody');
    const url = modalEl.dataset.url;
    const respondUrlTemplate = modalEl.dataset.respondUrl;
    let hasChanges = false;

    modalEl.addEventListener('show.bs.modal', () => {
        modalBody.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
        fetch(url)
            .then(res => res.text())
            .then(html => { modalBody.innerHTML = html; })
            .catch(() => {
                modalBody.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
            });
    });

    modalBody.addEventListener('click', async (e) => {
        const btn = e.target.closest('.btn-accept, .btn-decline');
        if (!btn) return;

        const isDecline = btn.classList.contains('btn-decline');
        if (isDecline && !confirm('Are you sure you want to decline this lesson request?')) return;

        const bookingId = btn.dataset.bookingId;
        const action = isDecline ? 'decline' : 'accept';
        const item = btn.closest('.request-item');
        const reqUrl = respondUrlTemplate.replace('/0/', `/${bookingId}/`);

        btn.disabled = true;

        try {
            const response = await csrfFetch(reqUrl, {
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
        if (hasChanges) window.location.reload();
    });
}

/* --------------------------------------------------------------------------
   7. Timetable Matrix Utilities & Regular Schedule
   -------------------------------------------------------------------------- */
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

function renderRegularScheduleGrid(data) {
    const grid = document.getElementById('regularScheduleGrid');
    if (!grid) return;

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

function initScheduleModal() {
    const modalEl = document.getElementById('scheduleModal');
    if (!modalEl) return;

    const startSelect = document.getElementById('dayEditStartSelect');
    const endSelect = document.getElementById('dayEditEndSelect');
    if (startSelect && endSelect) {
        startSelect.innerHTML = generateTimeOptions();
        endSelect.innerHTML = generateTimeOptions();
    }

    const url = modalEl.dataset.url;
    const grid = document.getElementById('regularScheduleGrid');
    const dayEditPanel = document.getElementById('dayEditPanel');
    const dayEditTitle = document.getElementById('dayEditTitle');
    const dayEditExistingRanges = document.getElementById('dayEditExistingRanges');
    const backBtn = document.getElementById('dayEditBackBtn');
    const addUrl = modalEl.dataset.addUrl;
    const addBtn = document.getElementById('dayEditAddBtn');
    const deleteUrlTemplate = modalEl.dataset.deleteUrl;

    let scheduleData = {};
    let currentDay = null;

    modalEl.addEventListener('show.bs.modal', () => {
        if (!grid) return;
        grid.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
        dayEditPanel.classList.add('d-none');
        grid.classList.remove('d-none');

        fetch(url)
            .then(res => res.json())
            .then(data => {
                scheduleData = data;
                renderRegularScheduleGrid(data);
            })
            .catch(() => {
                grid.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
            });
    });

    if (grid) {
        grid.addEventListener('click', (e) => {
            const column = e.target.closest('.schedule-day-column');
            if (!column) return;
            currentDay = column.dataset.day;
            openDayEditPanel(currentDay);
        });
    }

    if (backBtn) {
        backBtn.addEventListener('click', () => {
            dayEditPanel.classList.add('d-none');
            grid.classList.remove('d-none');        
        });
    }

    if (addBtn) {
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

                if (!scheduleData[currentDay]) scheduleData[currentDay] = [];
                scheduleData[currentDay].push({ id: data.id, start: data.start, end: data.end });

                renderExistingRanges(currentDay);
                renderRegularScheduleGrid(scheduleData);
            } catch (err) {
                alert('Network error. Please try again.');
            } finally {
                addBtn.disabled = false;
            }
        });
    }

    if (dayEditExistingRanges) {
        dayEditExistingRanges.addEventListener('click', async (e) => {
            const btn = e.target.closest('.remove-range-btn');
            if (!btn) return;
            if (!confirm('Remove this availability?')) return;

            const availabilityId = btn.dataset.id;
            const delUrl = deleteUrlTemplate.replace('/0/', `/${availabilityId}/`);
            btn.disabled = true;

            try {
                const response = await csrfFetch(delUrl, { method: 'POST' });
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
    }

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

/* --------------------------------------------------------------------------
   8. Timetable Matrix: Weekly Overrides
   -------------------------------------------------------------------------- */
function renderOverrideScheduleGrid(data) {
    const grid = document.getElementById('overrideScheduleGrid');
    if (!grid) return;

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
                if (isHourActive(hour, dayData.override_ranges)) cellClass = ' is-override';
            } else {
                if (isHourActive(hour, dayData.regular_ranges)) cellClass = ' is-available';
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

function initOverrideScheduleModal() {
    const modalEl = document.getElementById('scheduleModal');
    if (!modalEl) return;

    const startSelect = document.getElementById('overrideDayEditStartSelect');
    const endSelect = document.getElementById('overrideDayEditEndSelect');
    if (startSelect && endSelect) {
        startSelect.innerHTML = generateTimeOptions();
        endSelect.innerHTML = generateTimeOptions();
    }

    const url = modalEl.dataset.overrideUrl;
    const addUrl = modalEl.dataset.overrideAddUrl;
    const deleteUrlTemplate = modalEl.dataset.overrideDeleteUrl;

    const grid = document.getElementById('overrideScheduleGrid');
    const dayEditPanel = document.getElementById('overrideDayEditPanel');
    const dayEditTitle = document.getElementById('overrideDayEditTitle');
    const dayEditExistingRanges = document.getElementById('overrideDayEditExistingRanges');
    const backBtn = document.getElementById('overrideDayEditBackBtn');
    const addBtn = document.getElementById('overrideDayEditAddBtn');

    const prevBtn = document.getElementById('overrideWeekPrevBtn');
    const nextBtn = document.getElementById('overrideWeekNextBtn');
    const weekLabel = document.getElementById('overrideWeekRangeLabel');

    let scheduleData = {};
    let currentDate = null;
    let currentWeekStart = null;

    function loadWeek(weekStart) {
        grid.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
        dayEditPanel.classList.add('d-none');
        grid.classList.remove('d-none');

        const fetchUrl = weekStart ? `${url}?week_start=${weekStart}` : url;

        fetch(fetchUrl)
            .then(res => res.json())
            .then(data => {
                scheduleData = data.days;
                weekLabel.textContent = data.week_label;
                prevBtn.dataset.weekStart = data.prev_week_start;
                nextBtn.dataset.weekStart = data.next_week_start;
                renderOverrideScheduleGrid(scheduleData);
            })
            .catch(() => {
                grid.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
            });
    }

    modalEl.addEventListener('show.bs.modal', () => loadWeek(null));
    if (prevBtn) prevBtn.addEventListener('click', () => loadWeek(prevBtn.dataset.weekStart));
    if (nextBtn) nextBtn.addEventListener('click', () => loadWeek(nextBtn.dataset.weekStart));

    const overrideTabBtn = document.getElementById('override-tab');
    if (overrideTabBtn) {
        overrideTabBtn.addEventListener('shown.bs.tab', () => {
            loadWeek(currentWeekStart);
        });
    }

    if (grid) {
        grid.addEventListener('click', (e) => {
            const column = e.target.closest('.schedule-day-column');
            if (!column || column.classList.contains('schedule-day-disabled')) return;
            currentDate = column.dataset.date;
            openDayEditPanel(currentDate);
        });
    }

    if (backBtn) {
        backBtn.addEventListener('click', () => {
            dayEditPanel.classList.add('d-none');
            grid.classList.remove('d-none');

            if (prevBtn) prevBtn.classList.remove('d-none');
            if (nextBtn) nextBtn.classList.remove('d-none');
            if (weekLabel) weekLabel.classList.remove('d-none');

        });
    }

    if (addBtn) {
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
    }

    if (dayEditExistingRanges) {
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
    }

    function openDayEditPanel(dateStr) {
        const dateObj = new Date(dateStr + 'T00:00:00');
        dayEditTitle.textContent = dateObj.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
        renderExistingOverrideRanges(dateStr);
        grid.classList.add('d-none');
        dayEditPanel.classList.remove('d-none');

        if (prevBtn) prevBtn.classList.add('d-none');
        if (nextBtn) nextBtn.classList.add('d-none');
        if (weekLabel) weekLabel.classList.add('d-none');
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

/* --------------------------------------------------------------------------
   9. Teacher / Student Calendar (#30, #31)
   -------------------------------------------------------------------------- */
function initCalendarView() {
    const calendarPage = document.querySelector('.calendar-page');
    if (!calendarPage) return;

    const gridContainer = document.getElementById('calendar-grid-container');
    const monthLabel = document.querySelector('.week-range-label');
    const MAX_VISIBLE_PER_DAY = 3;

    function updateOverflow() {
        document.querySelectorAll('.calendar-day').forEach((day) => {
            const allRows = Array.from(day.querySelectorAll('.calendar-booking-row'));
            const visibleRows = allRows.filter((row) => !row.classList.contains('filter-hidden'));

            visibleRows.forEach((row, index) => {
                row.style.display = index >= MAX_VISIBLE_PER_DAY ? 'none' : '';
            });

            allRows.forEach((row) => {
                if (row.classList.contains('filter-hidden')) row.style.display = 'none';
            });

            const moreBtn = day.querySelector('.calendar-more-link');
            if (!moreBtn) return;

            const hiddenCount = visibleRows.length - MAX_VISIBLE_PER_DAY;
            if (hiddenCount > 0) {
                moreBtn.textContent = `+${hiddenCount} more`;
                moreBtn.style.display = 'block';
            } else {
                moreBtn.style.display = 'none';
            }
        });
    }

    function setupDropdown(buttonId, panelId) {
        const btn = document.getElementById(buttonId);
        const panel = document.getElementById(panelId);
        if (!btn || !panel) return;

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            panel.classList.toggle('open');
        });

        document.addEventListener('click', (e) => {
            if (!panel.contains(e.target) && !btn.contains(e.target)) {
                panel.classList.remove('open');
            }
        });
    }

    setupDropdown('status-filter-btn', 'status-filter-panel');
    setupDropdown('student-filter-btn', 'student-filter-panel');

    function applyFilters() {
        const checkedStatuses = Array.from(
            document.querySelectorAll('.status-checkbox:checked')
        ).map(cb => cb.value);

        const checkedStudents = Array.from(
            document.querySelectorAll('.student-checkbox:checked')
        ).map(cb => cb.value);

        document.querySelectorAll('.calendar-booking-row').forEach((row) => {
            const status = row.dataset.status;
            const studentId = row.dataset.studentId;
            const statusOk = checkedStatuses.includes(status);
            const studentOk = checkedStudents.length === 0 || checkedStudents.includes(studentId);

            row.classList.toggle('filter-hidden', !(statusOk && studentOk));
        });

        updateOverflow();
    }

    document.addEventListener('change', (e) => {
        if (e.target.matches('.status-checkbox') || e.target.matches('.student-checkbox')) {
            applyFilters();
        }
    });

    const studentSearch = document.getElementById('student-filter-search');
    if (studentSearch) {
        studentSearch.addEventListener('input', () => {
            const query = studentSearch.value.trim().toLowerCase();
            document.querySelectorAll('.student-filter-option').forEach((option) => {
                const name = option.textContent.trim().toLowerCase();
                option.style.display = name.includes(query) ? '' : 'none';
            });
        });
    }

    // Month Navigation AJAX
    function loadMonth(navUrl) {
        fetch(navUrl)
            .then(res => res.text())
            .then(html => {
                gridContainer.innerHTML = html;
                const newGrid = gridContainer.querySelector('.calendar-grid');
                if (newGrid && monthLabel) {
                    monthLabel.textContent = newGrid.dataset.monthLabel;
                }
                applyFilters();
            });
    }

    document.addEventListener('click', (e) => {
        const navBtn = e.target.closest('.week-nav-arrow');
        if (navBtn && navBtn.dataset.url) {
            loadMonth(navBtn.dataset.url);
        }
    });

    // "+N more" Day Popup
    const dayMorePopup = document.getElementById('dayMorePopup');
    const dayMorePopupHeader = document.getElementById('dayMorePopupHeader');
    const dayMorePopupList = document.getElementById('dayMorePopupList');

    function openDayMorePopup(dayCell) {
        const visibleRows = Array.from(
            dayCell.querySelectorAll('.calendar-booking-row')
        ).filter(row => !row.classList.contains('filter-hidden'));

        dayMorePopupHeader.textContent = dayCell.querySelector('.calendar-day-number').textContent;
        dayMorePopupList.innerHTML = '';
        visibleRows.forEach(row => {
            const clone = row.cloneNode(true);
            clone.style.display = '';
            dayMorePopupList.appendChild(clone);
        });

        const rect = dayCell.getBoundingClientRect();
        dayMorePopup.style.top = `${rect.top}px`;
        dayMorePopup.style.left = `${rect.left}px`;
        dayMorePopup.style.width = `${rect.width}px`;
        dayMorePopup.style.minHeight = `${rect.height}px`;
        dayMorePopup.classList.add('open');
    }

    document.addEventListener('click', (e) => {
        const moreBtn = e.target.closest('.calendar-more-link');
        if (moreBtn) {
            e.stopPropagation();
            openDayMorePopup(moreBtn.closest('.calendar-day'));
            return;
        }
        if (dayMorePopup && !dayMorePopup.contains(e.target)) {
            dayMorePopup.classList.remove('open');
        }
    });

    // Calendar Booking Click (Open modal)
    const modalEl = document.getElementById('lessonDetailModal');
    if (modalEl) {
        const modalBody = document.getElementById('lessonDetailModalBody');
        const urlTemplate = modalEl.dataset.url;
        const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);

        document.addEventListener('click', (e) => {
            const row = e.target.closest('.calendar-booking-row');
            if (!row) return;

            const bookingId = row.dataset.bookingId;
            const detailUrl = urlTemplate.replace('/0/', `/${bookingId}/`);

            modalBody.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
            modalInstance.show();

            fetch(detailUrl)
                .then(res => res.text())
                .then(html => { modalBody.innerHTML = html; })
                .catch(() => {
                    modalBody.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
                });
        });
    }

    applyFilters();
}

/* --------------------------------------------------------------------------
   10. Teacher Portfolio (Certificate AJAX Manager)
   -------------------------------------------------------------------------- */
function initCertificateManager() {
    const addForm = document.getElementById('certificateAddForm');
    const list = document.getElementById('certificatesList');
    if (!addForm || !list) return;

    const addUrl = addForm.dataset.addUrl;
    const deleteUrlTemplate = addForm.dataset.deleteUrl;

    addForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('certificateAddBtn');
        btn.disabled = true;

        try {
            const response = await csrfFetch(addUrl, {
                method: 'POST',
                body: new FormData(addForm),
            });
            const data = await response.json();

            if (!response.ok) {
                alert(data.error ? JSON.stringify(data.error) : 'Something went wrong.');
                btn.disabled = false;
                return;
            }

            const noMsg = document.getElementById('noCertificatesMsg');
            if (noMsg) noMsg.remove();

            const div = document.createElement('div');
            div.className = 'existing-range';
            div.dataset.id = data.id;
            const yearText = data.issue_date ? ` (${data.issue_date.slice(0, 4)})` : '';
            div.innerHTML = `<span>${data.title} — ${data.issued_by}${yearText}</span>
                <button type="button" class="remove-range-btn" data-id="${data.id}">⊗</button>`;
            list.appendChild(div);

            addForm.reset();
        } catch (err) {
            alert('Network error. Please try again.');
        } finally {
            btn.disabled = false;
        }
    });

    list.addEventListener('click', async (e) => {
        const btn = e.target.closest('.remove-range-btn');
        if (!btn) return;
        if (!confirm('Remove this certificate?')) return;

        const certId = btn.dataset.id;
        const url = deleteUrlTemplate.replace('/0/', `/${certId}/`);
        btn.disabled = true;

        try {
            const response = await csrfFetch(url, { method: 'POST' });
            if (!response.ok) {
                alert('Something went wrong. Please try again.');
                btn.disabled = false;
                return;
            }
            btn.closest('.existing-range').remove();
        } catch (err) {
            alert('Network error. Please try again.');
            btn.disabled = false;
        }
    });
}

function initPendingReviewsManager() {
    const modalEl = document.getElementById('pendingReviewsModal');
    if (!modalEl) return;

    const modalBody = document.getElementById('pendingReviewsModalBody');
    const url = modalEl.dataset.url;
    let hasChanges = false;

    modalEl.addEventListener('show.bs.modal', () => {
        modalBody.innerHTML = '<p class="text-muted text-center py-3">Loading...</p>';
        fetch(url)
            .then(res => res.text())
            .then(html => { modalBody.innerHTML = html; })
            .catch(() => {
                modalBody.innerHTML = '<p class="text-danger text-center py-3">Something went wrong. Please try again.</p>';
            });
    });

    modalBody.addEventListener('click', async (e) => {
        const btn = e.target.closest('.btn-accept, .btn-decline');
        if (!btn) return;

        const isReject = btn.classList.contains('btn-decline');
        if (isReject && !confirm('Are you sure you want to reject and delete this review?')) return;

        const reqUrl = isReject ? btn.dataset.rejectUrl : btn.dataset.approveUrl;
        const item = btn.closest('.request-item');
        btn.disabled = true;

        try {
            const response = await csrfFetch(reqUrl, { method: 'POST' });
            if (!response.ok) {
                alert('Something went wrong. Please try again.');
                btn.disabled = false;
                return;
            }

            item.remove();
            hasChanges = true;

            const badge = document.querySelector('button[data-bs-target="#pendingReviewsModal"] .badge-count');
            if (badge) {
                const newCount = parseInt(badge.textContent, 10) - 1;
                if (newCount > 0) {
                    badge.textContent = newCount;
                } else {
                    badge.remove();
                }
            }

            const remainingItems = modalBody.querySelectorAll('.request-item');
            if (remainingItems.length === 0) {
                modalBody.innerHTML = '<div class="empty-state"><p>No pending reviews.</p></div>';
            }
        } catch (err) {
            alert('Network error. Please try again.');
            btn.disabled = false;
        }
    });

    modalEl.addEventListener('hidden.bs.modal', () => {
        if (hasChanges) window.location.reload();
    });
}

/* --------------------------------------------------------------------------
   11. Single Application Bootstrap
   -------------------------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', () => {
    initSignupTimezoneDetection();
    initLessonCardsScroll();
    initWeekNav();
    initBookingModal();
    initLessonDetailModalManager();
    initLessonRequestsManager();
    initScheduleModal();
    initOverrideScheduleModal();
    initCalendarView();
    initCertificateManager();
    initPendingReviewsManager();
});