/* Patient portal AI helpers: "Explain with AI" on results/medications,
 * appointment preparation, term explainer, record summary. Static-first. */
(function () {
    'use strict';
    var lang = document.documentElement.getAttribute('lang') || 'en';
    function t(en, ar) { return lang === 'ar' ? ar : en; }
    function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
    function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.getAttribute('content') : ''; }
    var modal;
    function ensureModal() {
        if (modal) return modal;
        modal = document.createElement('div');
        modal.className = 'ai-panel-backdrop open';
        modal.id = 'patientAiModal';
        modal.innerHTML = '<div class="ai-modal" role="dialog" aria-modal="true" aria-labelledby="patientAiTitle"><div class="ai-panel-head"><div class="ai-panel-title" id="patientAiTitle"></div><button type="button" class="btn-icon" data-close aria-label="Close"><i class="fas fa-times"></i></button></div><div class="ai-panel-body"></div></div>';
        document.body.appendChild(modal);
        modal.addEventListener('click', function (e) { if (e.target === modal || e.target.closest('[data-close]')) hide(); });
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && modal && !modal.hidden) hide(); });
        return modal;
    }
    function hide() { if (modal) { modal.hidden = true; } }
    function show(title, html) {
        ensureModal();
        modal.hidden = false;
        modal.querySelector('#patientAiTitle').innerHTML = '<span class="ai-spark">✦</span> ' + esc(title);
        modal.querySelector('.ai-panel-body').innerHTML = html;
        var c = modal.querySelector('[data-close]'); if (c) c.focus();
    }
    function render(d) {
        if (!d.ok) return '<div class="ai-note ai-note-warn">' + esc(d.error || 'Error') + '</div>';
        var h = '<div class="ai-block ai-block-verified"><div class="ai-block-head"><span class="ai-label ai-label-verified">' + t('FROM YOUR RECORD & OUR LIBRARY', 'من سجلك ومكتبتنا') + '</span></div>' +
                (d.sections || []).map(function (s) { return '<div class="ai-sec"><div class="ai-sec-title">' + esc(s.title) + '</div><ul>' + (s.items || []).map(function (i) { return '<li>' + esc(i) + '</li>'; }).join('') + '</ul></div>'; }).join('') + '</div>';
        if (d.explanation) h += '<div class="ai-block ai-block-verified"><p>' + esc(d.explanation) + '</p></div>';
        if (d.ai) {
            if (d.ai.status === 'ok' || d.ai.status === 'cached') h += '<div class="ai-block"><div class="ai-block-head"><span class="ai-label ai-label-ai">✦ ' + t('AI EXPLANATION', 'شرح بالذكاء') + '</span></div><div class="ai-text">' + esc(d.ai.text).replace(/\n/g, '<br>') + '</div></div>';
            else h += '<div class="ai-note ai-note-muted">' + esc(d.ai.message || t('AI is not available right now; the information above still applies.', 'الذكاء غير متاح حاليًا؛ المعلومات أعلاه تبقى صحيحة.')) + '</div>';
        }
        h += '<div class="ai-footer">' + esc(d.footer || '') + '</div>';
        return h;
    }
    function call(url, payload, title) {
        show(title, '<div class="ai-loading"><i class="fas fa-circle-notch fa-spin"></i> ' + t('Preparing…', 'جارٍ التحضير…') + '</div>');
        fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }, body: JSON.stringify(payload || {}) })
            .then(function (r) { return r.json(); })
            .then(function (d) { show(d.title || title, render(d)); })
            .catch(function () { show(title, '<div class="ai-note ai-note-warn">' + t('The request failed.', 'فشل الطلب.') + '</div>'); });
    }
    document.addEventListener('click', function (e) {
        var el = e.target.closest('[data-pai]');
        if (!el) return;
        e.preventDefault();
        var kind = el.getAttribute('data-pai');
        if (kind === 'result') call('/ai/patient/explain-result', { order_id: el.getAttribute('data-id') }, t('Explain my result', 'اشرح نتيجتي'));
        else if (kind === 'medication') call('/ai/patient/explain-medication', { item_id: el.getAttribute('data-id') }, t('About my medicine', 'عن دوائي'));
        else if (kind === 'appointment') call('/ai/patient/prepare-appointment', { appointment_id: el.getAttribute('data-id') }, t('Prepare for my appointment', 'استعد لموعدي'));
        else if (kind === 'summary') call('/ai/patient/summary', {}, t('My health summary', 'ملخص صحتي'));
        else if (kind === 'questions') call('/ai/patient/questions', { concern: prompt(t('What is your main concern?', 'ما هو قلقك الرئيسي؟')) || '' }, t('Prepare questions', 'تحضير الأسئلة'));
        else if (kind === 'term') {
            var term = el.getAttribute('data-term') || prompt(t('Which medical term would you like explained?', 'ما المصطلح الطبي الذي تريد شرحه؟'));
            if (term) call('/ai/patient/explain-term', { term: term }, term);
        }
    });
})();
