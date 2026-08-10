

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