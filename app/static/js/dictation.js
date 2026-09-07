/* iHIS clinical dictation: a microphone button next to every note field.
 * Records with MediaRecorder, sends the clip to the local speech-to-text
 * endpoint (faster-whisper on this server; audio is never stored) and inserts
 * the transcript as an editable draft. Nothing is saved until the clinician
 * saves the form. */
(function () {
    'use strict';
    if (!window.MediaRecorder || !navigator.mediaDevices) return;
    var lang = document.documentElement.getAttribute('lang') || 'en';
    function t(en, ar) { return lang === 'ar' ? ar : en; }
    function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.getAttribute('content') : ''; }

    function attach(area) {
        if (area.dataset.dictationReady) return;
        area.dataset.dictationReady = '1';
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-ihs btn-ihs-ghost btn-ihs-sm dictate-btn';
        btn.innerHTML = '<i class="fas fa-microphone"></i> ' + t('Dictate', 'إملاء');
        btn.title = t('Local speech-to-text; audio never leaves the server', 'تحويل الكلام لنص محليًا؛ الصوت لا يغادر الخادم');
        area.insertAdjacentElement('afterend', btn);
        var rec = null, chunks = [], stream = null;
        btn.addEventListener('click', function () {
            if (rec && rec.state === 'recording') { rec.stop(); return; }
            navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
                stream = s; chunks = [];
                rec = new MediaRecorder(s);
                rec.ondataavailable = function (e) { if (e.data.size) chunks.push(e.data); };
                rec.onstop = function () {
                    stream.getTracks().forEach(function (tr) { tr.stop(); });
                    btn.disabled = true;
                    btn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> ' + t('Transcribing…', 'جارٍ التحويل…');
                    var fd = new FormData();
                    fd.append('audio', new Blob(chunks, { type: rec.mimeType || 'audio/webm' }), 'dictation.webm');
                    fd.append('language', lang);
                    fetch('/ai/copilot/transcribe', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf() }, body: fd })
                        .then(function (r) { return r.json(); })
                        .then(function (d) {
                            if (d.ok && d.text) {
                                area.value = (area.value ? area.value.replace(/\s+$/, '') + '\n' : '') + d.text;
                                area.dispatchEvent(new Event('input', { bubbles: true }));
                                area.focus();
                            } else if (window.ihisToast) { window.ihisToast(d.error || t('Transcription failed', 'فشل التحويل'), 'warning'); }
                        })
                        .catch(function () { if (window.ihisToast) window.ihisToast(t('Transcription failed', 'فشل التحويل'), 'warning'); })
                        .finally(function () { btn.disabled = false; btn.innerHTML = '<i class="fas fa-microphone"></i> ' + t('Dictate', 'إملاء'); btn.classList.remove('is-rec'); });
                };
                rec.start();
                btn.classList.add('is-rec');
                btn.innerHTML = '<i class="fas fa-stop"></i> ' + t('Stop', 'إيقاف');
            }).catch(function () { if (window.ihisToast) window.ihisToast(t('Microphone not available', 'الميكروفون غير متاح'), 'warning'); });
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        if (!document.body.hasAttribute('data-dictation')) return;
        document.querySelectorAll('textarea[data-copilot-target], textarea[data-ac]').forEach(attach);
    });
})();
