/* ============================================================
   iHIS Application JavaScript
   Command palette, toast system, sidebar, dark mode, lang toggle.
   ============================================================ */
(function () {
    'use strict';

    /* ---------- Dark Mode ---------- */
    const html = document.documentElement;
    const savedTheme = localStorage.getItem('ihis-theme');
    if (savedTheme) html.setAttribute('data-bs-theme', savedTheme);

    document.addEventListener('DOMContentLoaded', function () {
        const themeToggle = document.getElementById('themeToggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', function () {
                const current = html.getAttribute('data-bs-theme') || 'light';
                const next = current === 'dark' ? 'light' : 'dark';
                html.setAttribute('data-bs-theme', next);
                localStorage.setItem('ihis-theme', next);
                const icon = this.querySelector('i');
                if (icon) {
                    icon.className = next === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
                }
                this.classList.toggle('is-dark', next === 'dark');
            });
            const icon = themeToggle.querySelector('i');
            if (icon) {
                icon.className = (html.getAttribute('data-bs-theme') === 'dark') ? 'fas fa-sun' : 'fas fa-moon';
            }
        }

        /* ---------- Language Toggle ---------- */
        const langBtn = document.getElementById('langToggle');
        if (langBtn) {
            langBtn.addEventListener('click', function () {
                const current = html.getAttribute('dir') === 'rtl' ? 'ar' : 'en';
                const target = current === 'en' ? 'ar' : 'en';
                const url = new URL(window.location.href);
                url.searchParams.set('lang', target);
                window.location.href = url.toString();
            });
        }

        /* ---------- Sidebar groups: remember open/closed per group ---------- */
        (function () {
            var KEY = 'ihis-nav-groups';
            var state = {};
            try { state = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { state = {}; }
            document.querySelectorAll('details.nav-group[data-nav-key]').forEach(function (d) {
                var k = d.getAttribute('data-nav-key');
                if (d.hasAttribute('data-nav-active')) { d.open = true; return; }   // always show the current page
                if (Object.prototype.hasOwnProperty.call(state, k)) d.open = !!state[k];
                d.addEventListener('toggle', function () {
                    state[k] = d.open;
                    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode */ }
                });
            });
        })();

        /* ---------- Keyboard access to horizontally scrollable tables ---------- */
        document.querySelectorAll('.table-wrapper, .tabs-ihs, .care-strip, .scroll-area').forEach(function (el) {
            if (el.scrollWidth > el.clientWidth + 2 || el.scrollHeight > el.clientHeight + 2) { el.tabIndex = 0; if (!el.getAttribute('aria-label')) el.setAttribute('aria-label', 'Scrollable table'); }
        });

        /* ---------- Mobile Sidebar ---------- */
        const mobileToggle = document.getElementById('mobileToggle');
        const sidebar = document.getElementById('appSidebar');
        const backdrop = document.getElementById('sidebarBackdrop');

        if (mobileToggle && sidebar) {
            mobileToggle.addEventListener('click', function () {
                sidebar.classList.toggle('open');
                if (backdrop) backdrop.style.display = sidebar.classList.contains('open') ? 'block' : 'none';
            });
        }
        if (backdrop && sidebar) {
            backdrop.addEventListener('click', function () {
                sidebar.classList.remove('open');
                backdrop.style.display = 'none';
            });
        }

        /* ---------- Command Palette ---------- */
        const cmdOverlay = document.getElementById('cmdOverlay');
        const cmdInput = document.getElementById('cmdInput');
        const cmdTrigger = document.getElementById('cmdTrigger');

        function openCmd() {
            if (!cmdOverlay) return;
            cmdOverlay.classList.add('open');
            if (cmdInput) { cmdInput.value = ''; cmdInput.focus(); }
            document.body.style.overflow = 'hidden';
        }

        function closeCmd() {
            if (!cmdOverlay) return;
            cmdOverlay.classList.remove('open');
            document.body.style.overflow = '';
        }

        if (cmdTrigger) cmdTrigger.addEventListener('click', openCmd);
        if (cmdOverlay) {
            cmdOverlay.addEventListener('click', function (e) {
                if (e.target === cmdOverlay) closeCmd();
            });
        }

        document.addEventListener('keydown', function (e) {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                openCmd();
            }
            if (e.key === 'Escape') closeCmd();
        });

        /* ---------- Command Palette live search ---------- */
        const cmdResults = document.getElementById('cmdResults');

        function renderCmdResults(payload) {
            if (!cmdResults) return;
            cmdResults.innerHTML = '';
            const groups = payload.groups || {};
            const keys = ['patients', 'doctors', 'appointments', 'lab_orders',
                          'radiology_orders', 'prescriptions', 'referrals',
                          'documents', 'tasks'];
            const labels = {
                patients: 'Patients', doctors: 'Doctors', appointments: 'Appointments',
                lab_orders: 'Lab Orders', radiology_orders: 'Imaging',
                prescriptions: 'Prescriptions', referrals: 'Referrals',
                documents: 'Documents', tasks: 'Tasks'
            };
            let total = 0;
            for (const key of keys) {
                const items = groups[key] || [];
                if (!items.length) continue;
                total += items.length;
                const head = document.createElement('div');
                head.className = 'cmd-group';
                head.textContent = labels[key] + ' (' + items.length + ')';
                cmdResults.appendChild(head);
                items.forEach(function (item) {
                    const a = document.createElement('a');
                    a.href = item.url || '#';
                    a.className = 'cmd-result';
                    a.innerHTML = '<span class="cmd-result-title">' + escapeHtml(item.label) +
                        '</span><span class="cmd-result-sub">' + escapeHtml(item.subtitle || '') +
                        '</span>';
                    cmdResults.appendChild(a);
                });
            }
            if (!total) {
                cmdResults.innerHTML = '<div class="cmd-empty">No results</div>';
            }
        }

        function escapeHtml(s) {
            if (s == null) return '';
            return String(s).replace(/[&<>"']/g, function (c) {
                return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
            });
        }

        let cmdTimer = null;
        if (cmdInput && cmdResults) {
            cmdInput.addEventListener('input', function () {
                const q = cmdInput.value.trim();
                clearTimeout(cmdTimer);
                if (q.length < 2) { cmdResults.innerHTML = ''; return; }
                cmdTimer = setTimeout(function () {
                    fetch('/search?type=json&q=' + encodeURIComponent(q), {
                        headers: { 'X-Requested-With': 'XMLHttpRequest' }
                    })
                        .then(function (r) { return r.json(); })
                        .then(renderCmdResults)
                        .catch(function () { cmdResults.innerHTML = '<div class="cmd-empty">Search failed</div>'; });
                }, 220);
            });
        }

        /* ---------- Toast System ---------- */
        window.ihisToast = function (message, type) {
            type = type || 'info';
            let container = document.getElementById('toastContainer');
            if (!container) {
                container = document.createElement('div');
                container.id = 'toastContainer';
                container.className = 'toast-container';
                document.body.appendChild(container);
            }
            const toast = document.createElement('div');
            toast.className = 'toast-ihs toast-' + type;
            const icons = { success: 'fa-check-circle', danger: 'fa-exclamation-circle', warning: 'fa-exclamation-triangle', info: 'fa-info-circle' };
            toast.innerHTML = '<i class="fas ' + (icons[type] || icons.info) + '" style="color:var(--ihis-' + type + ')"></i><span>' + message + '</span>';
            container.appendChild(toast);
            setTimeout(function () {
                toast.style.opacity = '0';
                toast.style.transform = 'translateY(12px)';
                toast.style.transition = 'all 0.25s ease';
                setTimeout(function () { toast.remove(); }, 300);
            }, 4000);
        };

        /* Flash messages -> toasts */
        document.querySelectorAll('.alert-dismissible').forEach(function (alert) {
            const text = alert.textContent.trim();
            const cls = alert.className;
            let type = 'info';
            if (cls.includes('alert-success')) type = 'success';
            else if (cls.includes('alert-danger')) type = 'danger';
            else if (cls.includes('alert-warning')) type = 'warning';
            window.ihisToast(text, type);
            alert.remove();
        });

        /* ---------- Confirm modals for dangerous actions ---------- */
        document.querySelectorAll('[data-confirm]').forEach(function (el) {
            el.addEventListener('click', function (e) {
                if (!confirm(this.getAttribute('data-confirm'))) {
                    e.preventDefault();
                }
            });
        });
    });
})();
