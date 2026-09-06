/* iHIS AI Clinical Copilot panel + critical-alert drawer.
 * One entry point (✦ AI Copilot), contextual actions, explicit human review.
 * No AI request is made until the clinician clicks an action.            */
(function () {
    'use strict';
    var panel, backdrop, btn, body, lastFocus, patientId, panelData, lang;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function t(en, ar) { return lang === 'ar' ? ar : en; }
    function csrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }
    function post(url, data) {
        return fetch(url, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' },
            body: JSON.stringify(data || {})
        }).then(function (r) { return r.json().then(function (j) { j._http = r.status; return j; }); });
    }

    function statusPill(st) {
        if (!st) return '';
        var map = { READY: ['ai-pill-ready', t('AI READY', 'الذكاء جاهز')],
                    LIMITED: ['ai-pill-limited', t('AI LIMITED', 'الذكاء محدود')],
                    LIMIT_REACHED: ['ai-pill-limit', t('AI LIMIT REACHED', 'تم بلوغ حد الذكاء')],
                    UNAVAILABLE: ['ai-pill-off', t('AI UNAVAILABLE', 'الذكاء غير متاح')] };
        var m = map[st.state] || map.UNAVAILABLE;
        return '<span class="ai-pill ' + m[0] + '" title="' + esc(st.reason || '') + '">' + m[1] + '</span>';
    }

    function open() {
        if (!panel) return;
        lastFocus = document.activeElement;
        panel.classList.add('open');
        backdrop.classList.add('open');
        panel.setAttribute('aria-hidden', 'false');
        document.body.classList.add('ai-panel-open');
        load();
    }
    function close() {
        if (!panel) return;
        panel.classList.remove('open');
        backdrop.classList.remove('open');
        panel.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('ai-panel-open');
        if (lastFocus && lastFocus.focus) lastFocus.focus();
    }

    function load() {
        body.innerHTML = '<div class="ai-loading"><i class="fas fa-circle-notch fa-spin"></i> ' + t('Loading…', 'جارٍ التحميل…') + '</div>';
        var url = '/ai/copilot/panel' + (patientId ? '?patient_id=' + encodeURIComponent(patientId) : '');
        fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (d) { panelData = d; renderHome(d); })
            .catch(function () {
                body.innerHTML = '<div class="ai-note ai-note-warn">' + t('The Copilot could not load. Local clinical tools remain available.', 'تعذّر تحميل المساعد. الأدوات المحلية متاحة.') + '</div>';
            });
    }

    function renderHome(d) {
        var h = '';
        h += '<div class="ai-status-row">' + statusPill(d.status) +
             (d.status && d.status.state !== 'READY' ? '<div class="ai-note ai-note-muted">' + esc(d.status.reason) + '</div>' : '') + '</div>';
        if (d.patient) {
            h += '<div class="ai-context"><i class="fas fa-user-injured"></i> <strong>' + esc(d.patient.label) + '</strong>' +
                 (d.patient.mrn ? ' <span class="cell-muted">' + esc(d.patient.mrn) + '</span>' : '') +
                 ' <button type="button" class="btn-ihs btn-ihs-ghost btn-ihs-sm" data-ai-change-patient>' + t('Change', 'تغيير') + '</button></div>';
        } else {
            h += '<div class="ai-context ai-context-empty"><i class="fas fa-user-injured"></i> ' + t('No patient in context.', 'لا يوجد مريض في السياق.') +
                 (d.recent_patients && d.recent_patients.length ? ' ' + t('Choose one:', 'اختر:') + '<div class="ai-recent">' +
                 d.recent_patients.map(function (p) { return '<button type="button" class="ai-chip" data-ai-patient="' + p.id + '">' + esc(p.label) + '</button>'; }).join('') + '</div>' : '') + '</div>';
        }
        (d.groups || []).forEach(function (g) {
            h += '<details class="ai-group"' + (g.key === 'PREDICTIVE' ? ' open' : '') + '><summary><i class="fas ' + esc(g.icon) + '"></i> ' + esc(lang === 'ar' ? g.label_ar : g.label) +
                 (g.actions && g.actions.length ? ' <span class="ai-count">' + g.actions.length + '</span>' : '') + '</summary><div class="ai-group-body">';
            if (g.key === 'PREDICTIVE') {
                h += (g.predictive || []).map(function (p) {
                    var st = p.status === 'AVAILABLE' ? '<span class="ai-avail">' + t('AVAILABLE', 'متاح') + '</span>' : '<span class="ai-soon">' + t('COMING SOON', 'قريبًا') + '</span>';
                    var inner = '<i class="fas ' + esc(p.icon) + ' ai-card-icon"></i><div><div class="ai-card-title">' + esc(lang === 'ar' ? p.label_ar : p.label) + ' ' + st + '</div><div class="ai-card-desc">' + esc(lang === 'ar' ? p.desc_ar : p.desc) + '</div></div>';
                    return (p.allowed !== false && p.status === 'AVAILABLE') ? '<a class="ai-card" href="' + esc(p.url) + '">' + inner + '</a>' : '<div class="ai-card ai-card-off" aria-disabled="true">' + inner + '</div>';
                }).join('');
            } else {
                h += (g.actions || []).map(function (a) {
                    return '<button type="button" class="ai-action" data-ai-action="' + esc(a.key) + '"' + (d.patient ? '' : ' disabled') + '>' +
                           '<span class="ai-spark">✦</span> ' + esc(lang === 'ar' ? a.label_ar : a.label) + (a.heavy ? ' <span class="ai-heavy" title="' + t('Uses more AI budget', 'يستهلك ميزانية أكبر') + '">●</span>' : '') + '</button>';
                }).join('');
            }
            h += '</div></details>';
        });
        h += '<div class="ai-footer">' + esc(d.disclaimer || '') + '</div>';
        body.innerHTML = h;
        var first = body.querySelector('button, a, summary');
        if (first) first.focus();
    }

    var INPUT_ACTIONS = { 'reasoning.differential': 1, 'reasoning.questions': 1, 'reasoning.investigations': 1, 'doc.hpi': 1,
                          'doc.structure': 1, 'doc.assessment': 1, 'doc.referral': 1, 'comm.terms': 1 };

    function pageValue(sel) {
        var el = document.querySelector(sel);
        return el ? el.value : '';
    }

    function renderInputs(action) {
        var h = '<button type="button" class="btn-ihs btn-ihs-ghost btn-ihs-sm" data-ai-back><i class="fas fa-arrow-left"></i> ' + t('Back', 'رجوع') + '</button>';
        h += '<div class="ai-result-title"><span class="ai-spark">✦</span> ' + esc(actionLabel(action)) + '</div>';
        h += '<div class="ai-note ai-note-muted">' + t('Optional inputs. Nothing is written to the chart.', 'مدخلات اختيارية. لا يُكتب شيء في الملف.') + '</div>';
        var fields = [['complaint', t('Chief complaint', 'الشكوى الرئيسية'), pageValue('[name="reason"], [name="chief_complaint"], [name="complaint"]')],
                      ['history', t('History', 'التاريخ المرضي'), pageValue('[name="clinical_notes"], [name="hpi"], [name="history"]')],
                      ['exam', t('Examination', 'الفحص'), pageValue('[name="examination"], [name="examination_findings"], [name="exam"]')],
                      ['assessment', t('Working assessment', 'التقييم المبدئي'), pageValue('[name="diagnosis"], [name="assessment"]')]];
        if (action === 'doc.referral') fields = [['reason', t('Referral reason', 'سبب الإحالة'), pageValue('[name="reason"]')]];
        if (action === 'comm.terms') fields = [['text', t('Text to explain', 'النص المراد شرحه'), pageValue('[name="clinical_notes"], [name="diagnosis"]')]];
        h += '<form data-ai-form="' + esc(action) + '">' + fields.map(function (f) {
            return '<label class="ai-field"><span>' + esc(f[1]) + '</span><textarea name="' + f[0] + '" rows="2">' + esc(f[2]) + '</textarea></label>';
        }).join('') + '<button type="submit" class="btn-ihs btn-ihs-primary btn-ihs-sm"><span class="ai-spark">✦</span> ' + t('Run', 'تشغيل') + '</button></form>';
        body.innerHTML = h;
        body.querySelector('textarea, button').focus();
    }

    function actionLabel(key) {
        var lbl = key;
        (panelData && panelData.groups || []).forEach(function (g) {
            (g.actions || []).forEach(function (a) { if (a.key === key) lbl = lang === 'ar' ? a.label_ar : a.label; });
        });
        return lbl;
    }

    function runAction(action, inputs) {
        body.innerHTML = '<div class="ai-loading"><i class="fas fa-circle-notch fa-spin"></i> ' + t('Preparing verified data and asking the AI…', 'جارٍ تجهيز البيانات الموثقة وسؤال الذكاء…') + '</div>';
        post('/ai/copilot/run', { action: action, patient_id: patientId, inputs: inputs || {} })
            .then(renderResult)
            .catch(function () { body.innerHTML = '<div class="ai-note ai-note-warn">' + t('The request failed.', 'فشل الطلب.') + '</div>'; });
    }

    function sectionHtml(sec) {
        if (!sec.items || !sec.items.length) return '';
        if (sec.kind === 'text') return '<div class="ai-sec"><div class="ai-sec-title">' + esc(sec.title) + '</div><p>' + sec.items.map(esc).join('<br>') + '</p></div>';
        return '<div class="ai-sec"><div class="ai-sec-title">' + esc(sec.title) + '</div><ul>' + sec.items.map(function (i) { return '<li>' + esc(i) + '</li>'; }).join('') + '</ul></div>';
    }

    function aiHtml(ai, textForCopy) {
        if (!ai) return '';
        var h = '<div class="ai-block"><div class="ai-block-head"><span class="ai-label ai-label-ai">✦ ' + t('AI ASSISTED', 'بمساعدة الذكاء') + '</span>' +
                (ai.cached ? '<span class="ai-label ai-label-muted">' + t('cached', 'مخزّن') + '</span>' : '') + '</div>';
        if (ai.status === 'ok' || ai.status === 'cached') {
            if (ai.data && typeof ai.data === 'object') {
                h += renderJson(ai.data);
                textForCopy.text = JSON.stringify(ai.data, null, 2);
            } else {
                h += '<div class="ai-text">' + esc(ai.text || '').replace(/\n/g, '<br>') + '</div>';
                textForCopy.text = ai.text || '';
            }
            if (ai.injection_flag) h += '<div class="ai-note ai-note-warn">' + t('Possible instruction text was found inside patient data and ignored.', 'وُجد نص يشبه التعليمات داخل بيانات المريض وتم تجاهله.') + '</div>';
            h += '<div class="ai-actions-row">' +
                 '<button type="button" class="btn-ihs btn-ihs-outline btn-ihs-sm" data-ai-copy>' + t('Copy', 'نسخ') + '</button>' +
                 (document.querySelector('[data-copilot-target]') ? '<button type="button" class="btn-ihs btn-ihs-outline btn-ihs-sm" data-ai-insert>' + t('Insert into note', 'إدراج في الملاحظة') + '</button>' : '') +
                 (ai.usage_id ? '<span class="ai-feedback" data-usage="' + ai.usage_id + '"><button type="button" class="btn-ihs btn-ihs-ghost btn-ihs-sm" data-ai-fb="1" aria-label="Useful"><i class="fas fa-thumbs-up"></i></button><button type="button" class="btn-ihs btn-ihs-ghost btn-ihs-sm" data-ai-fb="0" aria-label="Not useful"><i class="fas fa-thumbs-down"></i></button></span>' : '') +
                 '</div>';
        } else {
            h += '<div class="ai-note ai-note-muted">' + esc(ai.message || t('AI unavailable; verified data shown above.', 'الذكاء غير متاح؛ البيانات الموثقة أعلاه.')) + '</div>';
        }
        return h + '</div>';
    }

    function renderJson(data) {
        var h = '';
        Object.keys(data).forEach(function (k) {
            var v = data[k];
            h += '<div class="ai-sec"><div class="ai-sec-title">' + esc(k.replace(/_/g, ' ')) + '</div>';
            if (Array.isArray(v)) {
                h += '<ul>' + v.map(function (item) {
                    if (item && typeof item === 'object') {
                        return '<li><strong>' + esc(item.name || item.term || item.title || '') + '</strong>' +
                               Object.keys(item).filter(function (kk) { return ['name', 'term', 'title'].indexOf(kk) < 0; }).map(function (kk) {
                                   var vv = item[kk];
                                   return '<div class="ai-sub"><em>' + esc(kk) + ':</em> ' + esc(Array.isArray(vv) ? vv.join('; ') : vv) + '</div>';
                               }).join('') + '</li>';
                    }
                    return '<li>' + esc(item) + '</li>';
                }).join('') + '</ul>';
            } else { h += '<p>' + esc(v) + '</p>'; }
            h += '</div>';
        });
        return h;
    }

    function renderResult(d) {
        if (!d.ok) { body.innerHTML = '<div class="ai-note ai-note-warn">' + esc(d.error || 'Error') + '</div>'; return; }
        var copy = { text: '' };
        var h = '<button type="button" class="btn-ihs btn-ihs-ghost btn-ihs-sm" data-ai-back><i class="fas fa-arrow-left"></i> ' + t('Back', 'رجوع') + '</button>';
        h += '<div class="ai-result-title"><span class="ai-spark">✦</span> ' + esc(lang === 'ar' ? (d.label_ar || d.label) : d.label) + ' ' + statusPill(d.status) + '</div>';
        h += '<div class="ai-block ai-block-verified"><div class="ai-block-head"><span class="ai-label ai-label-verified">' + t('VERIFIED CLINICAL DATA', 'بيانات سريرية موثقة') + '</span></div>' +
             (d.verified || []).map(sectionHtml).join('') + '</div>';
        h += aiHtml(d.ai, copy);
        h += '<div class="ai-footer">' + esc(d.disclaimer || '') + '</div>';
        body.innerHTML = h;
        body._copyText = copy.text;
        body.querySelector('[data-ai-back]').focus();
    }

    function bindEvents() {
        btn.addEventListener('click', function () { panel.classList.contains('open') ? close() : open(); });
        backdrop.addEventListener('click', close);
        panel.querySelector('[data-ai-close]').addEventListener('click', close);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && panel.classList.contains('open')) { e.preventDefault(); close(); }
            if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'a' || e.key === 'A')) { e.preventDefault(); open(); }
        });
        body.addEventListener('click', function (e) {
            var el = e.target.closest('[data-ai-action], [data-ai-back], [data-ai-patient], [data-ai-change-patient], [data-ai-copy], [data-ai-insert], [data-ai-fb]');
            if (!el) return;
            if (el.hasAttribute('data-ai-back')) { renderHome(panelData); return; }
            if (el.hasAttribute('data-ai-patient')) { patientId = el.getAttribute('data-ai-patient'); load(); return; }
            if (el.hasAttribute('data-ai-change-patient')) { patientId = null; load(); return; }
            if (el.hasAttribute('data-ai-copy')) {
                if (navigator.clipboard) navigator.clipboard.writeText(body._copyText || '');
                el.textContent = t('Copied', 'تم النسخ');
                return;
            }
            if (el.hasAttribute('data-ai-insert')) {
                var target = document.querySelector('[data-copilot-target]:focus') || document.querySelector('[data-copilot-target]');
                if (target) { target.value = (target.value ? target.value + '\n' : '') + '[AI DRAFT — review before signing]\n' + (body._copyText || ''); target.focus(); close(); }
                return;
            }
            if (el.hasAttribute('data-ai-fb')) {
                var wrap = el.closest('.ai-feedback');
                post('/ai/copilot/feedback', { usage_id: wrap.getAttribute('data-usage'), accepted: el.getAttribute('data-ai-fb') === '1' });
                wrap.innerHTML = '<span class="cell-muted">' + t('Thanks', 'شكرًا') + '</span>';
                return;
            }
            var action = el.getAttribute('data-ai-action');
            if (INPUT_ACTIONS[action]) renderInputs(action); else runAction(action, {});
        });
        body.addEventListener('submit', function (e) {
            var form = e.target.closest('[data-ai-form]');
            if (!form) return;
            e.preventDefault();
            var inputs = {};
            form.querySelectorAll('textarea, input').forEach(function (i) { if (i.value) inputs[i.name] = i.value; });
            runAction(form.getAttribute('data-ai-form'), inputs);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        panel = document.getElementById('copilotPanel');
        backdrop = document.getElementById('copilotBackdrop');
        btn = document.getElementById('copilotBtn');
        if (!panel || !btn) return;
        body = panel.querySelector('.ai-panel-body');
        lang = document.documentElement.getAttribute('lang') || 'en';
        patientId = document.body.getAttribute('data-copilot-patient') || null;
        bindEvents();
        document.querySelectorAll('[data-copilot-open]').forEach(function (el) {
            el.addEventListener('click', function (e) {
                e.preventDefault();
                var pid = el.getAttribute('data-copilot-open');
                if (pid) patientId = pid;
                open();
                var act = el.getAttribute('data-copilot-action');
                if (act) setTimeout(function () { if (INPUT_ACTIONS[act]) renderInputs(act); else runAction(act, {}); }, 400);
            });
        });
        // Deep link: ?copilot=<action> (retired AI pages redirect here)
        var wanted = new URLSearchParams(window.location.search).get('copilot');
        if (wanted && patientId) {
            open();
            setTimeout(function () { if (INPUT_ACTIONS[wanted]) renderInputs(wanted); else runAction(wanted, {}); }, 600);
        }
        // "Analyze with AI" on a result: opens the panel with the review
        document.addEventListener('click', function (e) {
            var el = e.target.closest('[data-result-review]');
            if (!el) return;
            e.preventDefault();
            open();
            body.innerHTML = '<div class="ai-loading"><i class="fas fa-circle-notch fa-spin"></i> ' + t('Reviewing result…', 'جارٍ مراجعة النتيجة…') + '</div>';
            post('/ai/copilot/result-review', { kind: el.getAttribute('data-result-review'), id: el.getAttribute('data-result-id') })
                .then(function (d) {
                    if (!d.ok) { body.innerHTML = '<div class="ai-note ai-note-warn">' + esc(d.error || 'Error') + '</div>'; return; }
                    d.label = d.title; d.label_ar = d.title; d.status = null;
                    renderResult(d);
                })
                .catch(function () { body.innerHTML = '<div class="ai-note ai-note-warn">' + t('The request failed.', 'فشل الطلب.') + '</div>'; });
        });
        // Critical alert drawer toggle (banner)
        var cb = document.getElementById('criticalBannerToggle');
        var cd = document.getElementById('criticalDrawer');
        if (cb && cd) {
            cb.addEventListener('click', function () { cd.hidden = !cd.hidden; cb.setAttribute('aria-expanded', String(!cd.hidden)); });
        }
    });
})();
