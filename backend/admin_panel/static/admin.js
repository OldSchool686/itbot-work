/* admin.js — Shared utilities for IT Support Bot Admin Panel */
const API = '/api/v1';

/* ---- Auth helpers (HttpOnly cookie-based) ---- */

async function checkAuth() {
    try {
        const resp = await fetch(`${API}/auth/me`, { credentials: 'include' });
        if (!resp.ok) throw new Error();
        const data = await resp.json();
        const el = document.getElementById('admin-name');
        if (el) el.textContent = data.username || 'Admin';
    } catch { window.location.href = '/admin/login'; return false; }

    return true;
}

async function logout() {
    try { await fetch(`${API}/auth/logout`, { method: 'POST', credentials: 'include' }); } catch {}
    document.cookie = 'admin_token=; path=/; max-age=0';
    window.location.href = '/admin/login';
}

async function apiFetch(path, opts = {}) {
    const headers = {};
    if (opts.body && typeof opts.body === 'string') headers['Content-Type'] = 'application/json';
    return fetch(`${API}${path}`, { ...opts, credentials: 'include', headers: { ...headers, ...opts.headers } });
}

/* ---- Toast notifications ---- */

function showToast(message, type) {
    const container = document.getElementById('toast-container');
    if (!container) { console.warn('[admin.js] toast-container not found. Creating one.'); createToastContainer(); }
    const el = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type || 'success'}`;
    toast.textContent = message;
    toast.setAttribute('role', 'alert');
    el.appendChild(toast);
    setTimeout(() => {
        toast.style.transition = 'opacity 0.3s';
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function createToastContainer() {
    const div = document.createElement('div');
    div.id = 'toast-container';
    div.className = 'toast-container';
    document.body.appendChild(div);
}

/* ---- Modal helpers ---- */

function openModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.remove('hidden');
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (modal) modal.classList.add('hidden');
}

/* ---- Phone formatting ---- */

function formatPhone(phone) {
    if (!phone) return '';
    let digits = phone.replace(/\D/g, '');
    if (digits.startsWith('8') && digits.length === 11) digits = '+7' + digits.slice(1);
    if (digits.startsWith('+7') && digits.length === 12) {
        const d = digits.slice(2);
        return `+7(${d.slice(0,3)}) ${d.slice(3,6)}-${d.slice(6,8)}-${d.slice(8)}`;
    }
    return phone;
}

function normalizePhone(phone) {
    let d = phone.replace(/\D/g, '');
    if (d.startsWith('8') && d.length === 11) d = '7' + d.slice(1);
    return '+7' + d;
}

/* ---- File utilities ---- */

function formatFileSize(bytes) {
    if (!bytes) return '\u2014';
    if (bytes < 1024) return bytes + ' \u0411';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' \u041a\u0411';
    return (bytes / 1048576).toFixed(1) + ' \u041c\u0411';
}

function getFileIcon(type) {
    const icons = { pdf: '\uD83D\uDCC4', docx: '\uD83D\uDCDD', xlsx: '\uD83D\uDCCA', txt: '\uD83D\uDCCB', md: '\uD83D\uDCC9', odt: '\uD83D\uDCDD' };
    return icons[type] || '\uD83D\uDCC4';
}

/* ---- Pagination ---- */

function renderPagination(total, perPage, current, callback) {
    const el = document.getElementById('pagination');
    if (!el) return;
    const totalPages = Math.ceil(total / perPage);
    if (totalPages <= 1) { el.innerHTML = ''; return; }

    let html = '';
    if (current > 1) html += `<a href="#" data-page="${current - 1}" aria-label="Предыдущая страница">&larr;</a>`;
    for (let i = Math.max(1, current - 2); i <= Math.min(totalPages, current + 2); i++) {
        if (i === current) html += `<span class="current">${i}</span>`;
        else html += `<a href="#" data-page="${i}">${i}</a>`;
    }
    if (current < totalPages) html += `<a href="#" data-page="${current + 1}" aria-label="Следующая страница">&rarr;</a>`;
    el.innerHTML = html;

    el.querySelectorAll('a[data-page]').forEach(a => {
        a.addEventListener('click', e => {
            e.preventDefault();
            callback(parseInt(a.dataset.page));
        });
    });
}

/* ---- CSV export ---- */

function exportCSV() {
    const link = document.createElement('a');
    link.href = `${API}/users/export-csv`;
    link.download = 'allowed_users.csv';
    // Use fetch to include auth header, then create blob download
    fetch(`${API}/users/export-csv`, { credentials: 'include' })
        .then(r => r.blob())
        .then(blob => {
            const url = URL.createObjectURL(blob);
            link.href = url;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);
        })
        .catch(() => showToast('Ошибка экспорта CSV', 'error'));
}

/* ---- Utilities ---- */

function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function formatDate(d) { if (!d) return ''; return new Date(d).toLocaleString('ru-RU'); }

/* ---- Theme Toggle (Dark/Light) ---- */

(function initTheme() {
    const saved = localStorage.getItem('admin_theme');
    if (saved === 'dark') document.body.setAttribute('data-theme', 'dark');
})();

function toggleTheme() {
    const isDark = document.body.getAttribute('data-theme') === 'dark';
    if (isDark) {
        document.body.removeAttribute('data-theme');
        localStorage.setItem('admin_theme', 'light');
    } else {
        document.body.setAttribute('data-theme', 'dark');
        localStorage.setItem('admin_theme', 'dark');
    }
}

/* ---- Font & Size Controls ---- */

(function initFontControls() {
    const saved = localStorage.getItem('admin_font');
    if (saved) document.body.setAttribute('data-font', saved);
    const size = localStorage.getItem('admin_font_size');
    if (size) document.documentElement.style.setProperty('--base-font-size', size + 'px');
})();

function setFont(font) {
    if (font) {
        document.body.setAttribute('data-font', font);
    } else {
        document.body.removeAttribute('data-font');
    }
    localStorage.setItem('admin_font', font || '');
}

function changeFontSize(delta) {
    let size = parseInt(localStorage.getItem('admin_font_size') || '15');
    size = Math.max(13, Math.min(19, size + delta));
    document.documentElement.style.setProperty('--base-font-size', size + 'px');
    localStorage.setItem('admin_font_size', size);
}

/* ---- Hamburger Menu ---- */

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (!sidebar) return;
    const isOpen = sidebar.classList.contains('open');
    if (isOpen) {
        sidebar.classList.remove('open');
        if (overlay) overlay.classList.remove('active');
    } else {
        sidebar.classList.add('open');
        if (overlay) overlay.classList.add('active');
    }
}

function closeSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('active');
}

/* ---- Stats Carousel ---- */

let carouselIndex = 0;
let carouselTimer = null;

function initCarousel() {
    const track = document.querySelector('.stats-carousel-track');
    if (!track) return;
    const cards = track.querySelectorAll('.stat-card');
    if (cards.length <= 2) return;

    const perSlide = window.innerWidth >= 640 ? 2 : 1;
    const maxIndex = Math.max(0, cards.length - perSlide);

    function slide() {
        const cardWidth = cards[0].offsetWidth + 16;
        track.style.transform = `translateX(-${carouselIndex * cardWidth}px)`;
    }

    document.querySelector('.carousel-btn.prev')?.addEventListener('click', () => {
        carouselIndex = Math.max(0, carouselIndex - perSlide);
        slide(); resetAutoSlide();
    });
    document.querySelector('.carousel-btn.next')?.addEventListener('click', () => {
        carouselIndex = Math.min(maxIndex, carouselIndex + perSlide);
        slide(); resetAutoSlide();
    });

    function autoSlide() {
        carouselTimer = setInterval(() => {
            carouselIndex = carouselIndex >= maxIndex ? 0 : Math.min(maxIndex, carouselIndex + perSlide);
            slide();
        }, 5000);
    }
    function resetAutoSlide() { clearInterval(carouselTimer); autoSlide(); }

    window.addEventListener('resize', () => { carouselIndex = 0; slide(); });
    slide(); autoSlide();
}

/* ---- Global keyboard shortcuts ---- */

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay:not(.hidden)').forEach(m => m.classList.add('hidden'));
        closeSidebar();
    }
});

/* ---- Pause animations when tab is hidden (saves CPU) ---- */

document.addEventListener('visibilitychange', () => {
    document.body.classList.toggle('paused-animations', document.hidden);
});
