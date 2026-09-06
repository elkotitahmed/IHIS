/* iHIS smart clinical autocomplete + smart diagnosis entry.
 * LOCAL FIRST -> cache -> AI (debounced, cancellable, never per keystroke).
 * Keys: ArrowDown/ArrowUp select, Enter confirm, Tab accept, Esc dismiss.  */
(function () {
    'use strict';
    var cache = {}, menu, active = null, items = [], index = -1, aiTimer, localTimer, aiCtrl, aiSuggestion = null, aiEnabled = null;
    var lang = document.documentElement.getAttribute('lang') || 'en';
    function t(en, ar) { return lang === 'ar' ? ar : en; }
    function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
    function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.getAttribute('content') : ''; }

    function ensureMenu() {
        if (menu) return menu;
        menu = document.createElement('div');
        menu.className = 'ac-menu';
        menu.setAttribute('role', 'listbox');
        menu.hidden = true;
        document.body.appendChild(menu);
        menu.addEventListener('mousedown', function (e) {
            var li = e.target.closest('[data-ac-index]');
            if (!li) return;
            e.preventDefault();
            accept(parseInt(li.getAttribute('data-ac-index'), 10));
        });
        return menu;
    }

    function position(el) {
        var r = el.getBoundingClientRect();
        menu.style.top = (window.scrollY + r.bottom + 4) + 'px';
        menu.style.left = (window.scrollX + r.left) + 'px';
        menu.style.width = Math.max(240, r.width) + 'px';
    }

    function currentToken(el) {
        var v = el.value.slice(0, el.selectionStart);
        var m = v.match(/([A-Za-z؀-ۿ][A-Za-z؀-ۿ0-9 \-]{0,40})$/);
        var tok = m ? m[1] : '';
        var parts = tok.split(' ');
        return parts.slice(-3).join(' ').replace(/^\s+/, '');
    }

    function show(el, list, ai) {
        ensureMenu();
        items = list.slice(0, 8);
        aiSuggestion = ai || null;
        if (!items.length && !aiSuggestion) { hide(); return; }
        var h = items.map(function (s, i) { return '<div class="ac-item" role="option" data-ac-index="' + i + '">' + esc(s) + '</div>'; }).join('');
        if (aiSuggestion) h += '<div class="ac-item ac-item-ai" role="option" data-ac-index="' + items.length + '"><span class="ai-spark">✦</span> <span class="ai-label ai-label-ai">' + t('AI SUGGESTION', 'اقتراح الذكاء') + '</span> ' + esc(aiSuggestion) + '</div>';
        h += '<div class="ac-hint">' + t('Tab accept · Esc dismiss · ↑↓ select · Enter confirm', 'Tab قبول · Esc إغلاق · ↑↓ اختيار · Enter تأكيد') + '</div>';
        menu.innerHTML = h;
        index = 0;
        highlight();
        position(el);
        menu.hidden = false;
        el.setAttribute('aria-expanded', 'true');
    }
    function hide() {
        if (menu) menu.hidden = true;
        if (active) active.setAttribute('aria-expanded', 'false');
        index = -1;
    }
    function highlight() {
        if (!menu) return;
        menu.querySelectorAll('.ac-item').forEach(function (n, i) { n.classList.toggle('active', i === index); n.setAttribute('aria-selected', String(i === index)); });
    }
    function accept(i) {
        if (!active) return;
        var el = active;
        var total = items.length + (aiSuggestion ? 1 : 0);
        if (i < 0 || i >= total) return;
        var start = el.selectionStart, before = el.value.slice(0, start), after = el.value.slice(start);
        if (i < items.length) {
            var tok = currentToken(el);
            var cut = before.length - tok.length;
            var sep = (before.slice(0, cut) && !/\s$/.test(before.slice(0, cut))) ? ' ' : '';
            el.value = before.slice(0, cut) + sep + items[i] + ' ' + after;
            el.selectionStart = el.selectionEnd = (before.slice(0, cut) + sep + items[i] + ' ').length;
        } else {
            var sp = /\s$/.test(before) || !before ? '' : ' ';
            el.value = before + sp + aiSuggestion + after;
            el.selectionStart = el.selectionEnd = (before + sp + aiSuggestion).length;
        }
        el.dispatchEvent(new Event('input', { bubbles: true }));
        hide();
    }

    function localLookup(el) {
        var field = el.getAttribute('data-ac') || 'hpi';
        var q = currentToken(el);
        if (q.length < 2) { hide(); return; }
        var key = field + '|' + q.toLowerCase();
        if (cache[key]) { show(el, cache[key], aiSuggestion); return; }
        fetch('/ai/copilot/autocomplete?field=' + encodeURIComponent(field) + '&q=' + encodeURIComponent(q), { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (aiEnabled === null) aiEnabled = !!d.ai_enabled;
                cache[key] = d.suggestions || [];
                if (active === el) show(el, cache[key], aiSuggestion);
            }).catch(function () {});
    }

    function aiLookup(el) {
        if (aiEnabled === false || el.getAttribute('data-ac-ai') === 'off') return;
        var text = el.value.slice(0, el.selectionStart);
        if (text.trim().length < 12) return;
        var key = 'ai|' + (el.getAttribute('data-ac') || 'hpi') + '|' + text.slice(-200);
        if (cache[key] !== undefined) { if (cache[key]) show(el, items, cache[key]); return; }
        if (aiCtrl) aiCtrl.abort();
        aiCtrl = new AbortController();
        fetch('/ai/copilot/autocomplete/ai', {
            method: 'POST', credentials: 'same-origin', signal: aiCtrl.signal,
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
            body: JSON.stringify({ field: el.getAttribute('data-ac') || 'hpi', text: text, patient_id: el.getAttribute('data-ac-patient') || document.body.getAttribute('data-copilot-patient') || null })
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (d.status === 'limited' || d.status === 'unavailable') aiEnabled = false;
            cache[key] = d.suggestion || null;
            if (active === el && d.suggestion) show(el, items, d.suggestion);
        }).catch(function () {});
    }

    function attach(el) {
        el.setAttribute('autocomplete', 'off');
        el.setAttribute('aria-autocomplete', 'list');
        el.setAttribute('aria-expanded', 'false');
        el.addEventListener('focus', function () { active = el; });
        el.addEventListener('input', function () {
            active = el;
            aiSuggestion = null;
            clearTimeout(localTimer); clearTimeout(aiTimer);
            localTimer = setTimeout(function () { localLookup(el); }, 120);
            aiTimer = setTimeout(function () { aiLookup(el); }, 1200);
        });
        el.addEventListener('keydown', function (e) {
            var openMenu = menu && !menu.hidden && active === el;
            if (e.key === 'Escape') { if (openMenu) { e.preventDefault(); hide(); } return; }
            if (!openMenu) return;
            var total = items.length + (aiSuggestion ? 1 : 0);
            if (e.key === 'ArrowDown') { e.preventDefault(); index = (index + 1) % total; highlight(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); index = (index - 1 + total) % total; highlight(); }
            else if (e.key === 'Tab') { e.preventDefault(); accept(index < 0 ? 0 : index); }
            else if (e.key === 'Enter') { e.preventDefault(); accept(index < 0 ? 0 : index); }
        });
        el.addEventListener('blur', function () { setTimeout(function () { if (active === el) hide(); }, 150); });
    }

    /* ---------------- Smart diagnosis entry ---------------- */
    function attachDx(input) {
        var codeTarget = input.getAttribute('data-dx-code-target');
        var codeEl = codeTarget ? document.querySelector(codeTarget) : null;
        var box = document.createElement('div');
        box.className = 'dx-menu';
        box.hidden = true;
        input.parentNode.insertBefore(box, input.nextSibling);
        var timer;
        function render(d) {
            var groups = [['terminology', t('Terminology', 'المصطلحات')], ['recent', t('Recent', 'الأخيرة')], ['favorites', t('Favorites', 'المفضلة')], ['ai', t('✦ AI suggestions (select explicitly)', '✦ اقتراحات الذكاء (اختر صراحةً)')]];
            var h = '';
            groups.forEach(function (g) {
                var list = d[g[0]] || [];
                if (!list.length) return;
                h += '<div class="dx-group">' + esc(g[1]) + '</div>' + list.map(function (x) {
                    return '<button type="button" class="dx-item" data-code="' + esc(x.code || '') + '" data-term="' + esc(x.term) + '">' +
                           (x.code ? '<span class="dx-code">' + esc(x.code) + '</span>' : '') + esc(x.term) + (x.why ? ' <span class="cell-muted">— ' + esc(x.why) + '</span>' : '') + '</button>';
                }).join('');
            });
            box.innerHTML = h || '<div class="dx-group">' + t('No local match', 'لا توجد مطابقة محلية') + '</div>';
            box.hidden = false;
        }
        input.addEventListener('input', function () {
            clearTimeout(timer);
            var q = input.value.trim();
            if (q.length < 2) { box.hidden = true; return; }
            timer = setTimeout(function () {
                fetch('/ai/copilot/diagnosis-lookup?q=' + encodeURIComponent(q), { credentials: 'same-origin' })
                    .then(function (r) { return r.json(); }).then(function (d) { d.ai = box._ai || []; render(d); }).catch(function () {});
            }, 150);
        });
        input.addEventListener('keydown', function (e) { if (e.key === 'Escape') box.hidden = true; });
        box.addEventListener('click', function (e) {
            var b = e.target.closest('.dx-item');
            if (!b) return;
            input.value = b.getAttribute('data-term');
            if (codeEl) codeEl.value = b.getAttribute('data-code') || '';
            box.hidden = true;
            input.dispatchEvent(new Event('change', { bubbles: true }));
        });
        document.addEventListener('click', function (e) { if (!box.contains(e.target) && e.target !== input) box.hidden = true; });
        var aiBtn = input.getAttribute('data-dx-ai') ? document.querySelector(input.getAttribute('data-dx-ai')) : null;
        if (aiBtn) {
            aiBtn.addEventListener('click', function () {
                var src = input.getAttribute('data-dx-source') ? document.querySelector(input.getAttribute('data-dx-source')) : null;
                var text = (src && src.value) || input.value;
                aiBtn.disabled = true;
                fetch('/ai/copilot/diagnosis-suggest', { method: 'POST', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                    body: JSON.stringify({ text: text, patient_id: document.body.getAttribute('data-copilot-patient') || null }) })
                    .then(function (r) { return r.json(); }).then(function (d) {
                        aiBtn.disabled = false;
                        box._ai = d.suggestions || [];
                        render({ terminology: d.local || [], ai: box._ai });
                        if (!(d.suggestions || []).length && d.message) box.innerHTML += '<div class="dx-group">' + esc(d.message) + '</div>';
                    }).catch(function () { aiBtn.disabled = false; });
            });
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('textarea[data-ac], input[data-ac]').forEach(attach);
        document.querySelectorAll('input[data-dx-lookup]').forEach(attachDx);
        window.addEventListener('scroll', function () { if (menu && !menu.hidden && active) position(active); }, true);
    });
})();
