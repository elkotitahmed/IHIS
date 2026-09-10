"""Build the illustrated Arabic user guides (one per role) and the judging
walkthrough as PDFs: screenshots are taken in a real browser against a local
server (AI disabled, rate limiting off), composed into RTL HTML and printed
with headless Chrome.

    python scripts/make_guides.py            # -> docs/guides/*.pdf + docs/guides/shots/*.png

Requires the seeded demo database (python seed.py) and Playwright's Chromium.
"""
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'docs', 'guides')
SHOTS = os.path.join(OUT, 'shots')
CHROME = [p for p in (r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe') if os.path.exists(p)]
ACCOUNTS = {'physician': 'dr.ahmed@ihis.com', 'patient': 'patient@ihis.com', 'pharmacist': 'pharma@ihis.com',
            'nurse': 'nurse@ihis.com', 'radiologist': 'radio@ihis.com', 'reception': 'reception@ihis.com',
            'superadmin': 'superadmin@ihis.com'}

# role -> (title, intro, [(path, caption, what-to-do)])
GUIDES = {
    'physician': ('دليل الطبيب', 'كل ما يحتاجه الطبيب في يوم عمل: لوحة اليوم، ملف المريض 360، المساعد السريري الذكي، وإدخال سجل طبي ببحث تشخيص ذكي.', [
        ('/doctor/dashboard', 'لوحة الطبيب', 'أرقام اليوم في أعلى الصفحة: مرضى اليوم، الانتظار، التنبيهات الحرجة، النتائج المعلقة. شريط الذكاء يعرض المساعد وحالته والأدوات التنبؤية ومحركات السلامة.'),
        ('/doctor/dashboard#copilot', 'المساعد السريري الذكي', 'زر "المساعد الذكي" في القائمة الجانبية يفتح لوحة المساعد: اختر المريض ثم إجراءً (ملخص، تشخيص تفريقي، هيكلة الملاحظة، مراجعة السلامة، تواصل المريض). كل إجراء صريح ولا يُكتب في الملف تلقائيًا.'),
        ('/clinical/patient/1', 'ملف المريض 360', 'رأس المريض يعرض الحساسية والمشاكل النشطة والتنبيهات المفتوحة وNEWS2 قبل أي إجراء. الخط الزمني يجمع الزيارات والنتائج والوصفات.'),
        ('/doctor/patients/1/emr/add', 'إدخال سجل طبي', 'اكتب "hyper" في حقل التشخيص لتظهر Hypertension (I10) أولًا من قائمة ICD-10 الرسمية الكاملة. حقول الملاحظات فيها إكمال ذكي وزر إملاء صوتي محلي.'),
        ('/clinical/inbox', 'صندوق الوارد الذكي', 'كل ما يحتاج انتباهك: نتائج حرجة، تنبيهات، إحالات، تدخلات صيدلة. زر "ما الذي يحتاج انتباهي أولًا؟" يرتّبها بقواعد حتمية.'),
        ('/ai/hub', 'مركز الذكاء الاصطناعي', 'كل أداة ذكاء متاحة لدورك بحالتها الحقيقية: متاح / محدود / قريبًا.'),
    ]),
    'patient': ('دليل المريض', 'بوابة بسيطة بلغة واضحة: صحتي، مواعيدي، أدويتي، نتائجي، ومساعد صحي يشرح ولا يشخّص.', [
        ('/patient/dashboard', 'لوحة المريض', 'بطاقات واضحة لكل ما يخصك، والمساعد الصحي في جانب الصفحة.'),
        ('/patient/health-summary', 'ملخص صحتي', 'حالاتك النشطة وأدويتك ونتائجك ومواعيد المتابعة في صفحة واحدة، مع أزرار "اشرح لي" بلغة بسيطة.'),
        ('/patient/appointments', 'مواعيدي', 'المواعيد القادمة والسابقة، وحجز موعد جديد.'),
        ('/patient/prescriptions', 'أدويتي', 'الأدوية النشطة مع شرح مبسّط لكل دواء عند الطلب.'),
        ('/patient/messages', 'رسائلي', 'تواصل مع طبيبك من داخل البوابة.'),
    ]),
    'pharmacist': ('دليل الصيدلي', 'صف الصرف، فحص السلامة لكل وصفة، فحص التداخلات على 160 ألف زوج مرجعي، والمرجع الدوائي الرسمي.', [
        ('/pharmacy/dashboard', 'لوحة الصيدلية', 'المخزون والصرف وأدوات الذكاء الخاصة بالصيدلة.'),
        ('/pharmacy/prescriptions', 'صف الصرف', 'كل وصفة بأصنافها وحالتها؛ زر "فحص السلامة" يفتح الوصفة مع رأس السلامة (الحساسية، المشاكل، التنبيهات).'),
        ('/pharmacy/drug-check', 'فحص التداخلات', 'اختر دواءين أو أكثر؛ النتائج من دليل المستشفى ومن مرجع DDInter بدرجة الخطورة.'),
        ('/pharmacy/medications', 'دليل الأدوية', 'زر "النشرة" يفتح النشرة الرسمية (openFDA) ومعرّف RxNorm والتداخلات الموثقة.'),
        ('/pharmacy/ai-workbench', 'الصيدلاني السريري الذكي', 'مراجعة العلاج الدوائي للمرضى ذوي الوصفات المعلقة.'),
    ]),
    'nurse': ('دليل التمريض', 'مرضاك، الجرعات المستحقة، العلامات الحيوية مع NEWS2 التلقائي، وسجل إعطاء الدواء.', [
        ('/nursing/dashboard', 'لوحة التمريض', 'المرضى المسندون، الجرعات المستحقة، الملاحظات غير الطبيعية، والعلامات الحيوية المستحقة.'),
        ('/nursing/patients/1/vitals', 'العلامات الحيوية وNEWS2', 'عند حفظ القياس يُحسب NEWS2 وqSOFA تلقائيًا؛ الدرجة العالية تُنشئ تنبيهًا للطبيب.'),
        ('/nursing/patients/1/mar', 'سجل إعطاء الدواء (MAR)', 'كل جرعة بحالتها: أُعطيت، مُوقفة، رفضها المريض.'),
        ('/nursing/medication-schedule', 'جدول الأدوية', 'الجرعات المستحقة عبر كل المرضى.'),
    ]),
    'radiologist': ('دليل أخصائي الأشعة', 'قائمة العمل، إدخال التقرير، محرك النتائج الحرجة، وفحص أشعة الصدر بالذكاء.', [
        ('/radiology/dashboard', 'لوحة الأشعة', 'الطلبات المعلقة والمجدولة والمكتملة، وأدوات الذكاء.'),
        ('/radiology/orders', 'قائمة العمل', 'كل طلب بحالته؛ إدخال التقرير أو عرضه.'),
        ('/ai/copilot/radiology', 'ذكاء الأشعة', 'كل تقرير موقّع يُفحص بحثًا عن نتيجة حرجة: تنبيه ← مهمة عاجلة ← إشعار الطبيب ← إقرار ← تدقيق.'),
        ('/ai/chest-xray', 'فحص أشعة الصدر', 'ارفع صورة صدر أمامية لتحصل على احتمالات 18 نتيجة؛ النتائج الحرجة الواثقة تُنشئ تنبيهًا.'),
        ('/ai/fracture-detection', 'كشف الكسور', 'ارفع أشعة عظام؛ يعلّم النموذج مناطق الكسر المحتملة ويؤكدها الأخصائي.'),
    ]),
    'reception': ('دليل الاستقبال', 'تسجيل المرضى، الحجز، قائمة الانتظار.', [
        ('/reception/dashboard', 'لوحة الاستقبال', 'مواعيد اليوم والمنتظرون والمسجّلون.'),
        ('/reception/register', 'تسجيل مريض', 'نموذج تسجيل مريض جديد.'),
        ('/reception/appointments/book', 'حجز موعد', 'اختيار الطبيب والوقت ونوع الزيارة.'),
        ('/reception/queue', 'قائمة الانتظار', 'ترتيب الحضور وتسجيل الوصول.'),
    ]),
    'superadmin': ('دليل المشرف العام', 'مركز القيادة، مركز التحكم بالذكاء، السعة والغياب، الأدوار والصلاحيات، والعرض التوضيحي.', [
        ('/super-admin/dashboard', 'مركز القيادة', 'نظرة على المستشفى كله، وشريط الذكاء في أعلى الصفحة.'),
        ('/super-admin/ai-control', 'مركز التحكم بالذكاء', 'الاستخدام والميزانية والذاكرة المؤقتة والإخفاقات والتدقيق، بدون أي نصوص مرضى.'),
        ('/admin/capacity', 'السعة والغياب', 'إشغال كل طبيب في الأسبوع القادم ومعدل الغياب وتوقع الغياب من بيانات المستشفى.'),
        ('/super-admin/roles', 'الأدوار والصلاحيات', 'من يقدر يعمل إيه.'),
    ]),
}


def start_server():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, PORT=str(port), FLASK_CONFIG='development', AI_ENABLED='0', PYTHONIOENCODING='utf-8')
    proc = subprocess.Popen([sys.executable, '-c', f"import run; from app import limiter; limiter.enabled = False; run.app.run(port={port}, debug=False, use_reloader=False)"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    for _ in range(120):
        try:
            if urllib.request.urlopen(url + '/health/live', timeout=1).status == 200:
                return proc, url
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    proc.kill(); raise RuntimeError('server did not start')


def shoot(url):
    from playwright.sync_api import sync_playwright
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(); ctx = b.new_context(viewport={'width': 1366, 'height': 820}, locale='ar')
        page = ctx.new_page()
        for role, (title, intro, steps) in GUIDES.items():
            page.goto(url + '/auth/logout'); page.goto(url + '/auth/login')
            page.fill('input[type=email]', ACCOUNTS[role]); page.fill('input[type=password]', '123456')
            page.click('button[type=submit], input[type=submit]'); page.wait_for_load_state('networkidle')
            for i, (path, cap, _) in enumerate(steps, 1):
                target = path.split('#')[0] + ('&' if '?' in path else '?') + 'lang=ar'
                page.goto(url + target); page.wait_for_load_state('networkidle')
                if path.endswith('#copilot'):
                    try:
                        page.click('.sb-ai-btn'); page.wait_for_timeout(700)
                    except Exception:  # noqa: BLE001
                        pass
                page.wait_for_timeout(300)
                page.screenshot(path=os.path.join(SHOTS, f'{role}_{i}.png'), full_page=False)
                print('shot', role, i, cap)
        b.close()


def html_guide(role, title, intro, steps):
    parts = [f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>iHIS — {title}</title>
<style>@page{{size:A4;margin:14mm}} body{{font-family:'Segoe UI',Tahoma,Arial,sans-serif;color:#0f172a;font-size:11.5pt;line-height:1.7;margin:0}}
h1{{font-size:22pt;margin:0 0 4px}} .sub{{color:#475569;font-size:10.5pt;margin-bottom:14px}} .step{{page-break-inside:avoid;border:1px solid #e6e8ee;border-radius:12px;padding:12px 14px;margin:0 0 14px}}
.step h2{{font-size:13pt;margin:0 0 6px;color:#0a5241}} .step h2 span{{display:inline-block;background:#0e7a5f;color:#fff;border-radius:999px;width:26px;height:26px;text-align:center;line-height:26px;font-size:11pt;margin-left:8px}}
.step img{{width:100%;border:1px solid #e6e8ee;border-radius:8px;margin:8px 0}} .step p{{margin:0}} .kicker{{display:inline-block;font-size:9pt;font-weight:700;color:#5b21b6;background:#f4f1ff;border:1px solid #ddd6fe;padding:2px 10px;border-radius:999px;margin-bottom:8px}}
.foot{{font-size:9pt;color:#64748b;border-top:1px dashed #e6e8ee;padding-top:8px;margin-top:10px}}</style></head><body>
<span class="kicker">iHIS · دليل مستخدم مصوّر · 5 دقائق</span><h1>{title}</h1><div class="sub">{intro}</div>"""]
    for i, (path, cap, what) in enumerate(steps, 1):
        parts.append(f'<div class="step"><h2><span>{i}</span>{cap} <small style="color:#64748b;font-weight:400;direction:ltr;unicode-bidi:embed">{path.split("#")[0]}</small></h2><img src="shots/{role}_{i}.png" alt="{cap}"><p>{what}</p></div>')
    parts.append('<div class="foot">الدخول التجريبي: كلمة السر 123456 لكل الحسابات التجريبية. الذكاء الاصطناعي يشرح ويقترح فقط؛ القواعد الحتمية هي المرجع والطبيب يقرر. أُنشئ هذا الدليل تلقائيًا من النظام الحقيقي.</div></body></html>')
    return '\n'.join(parts)


def to_pdf(html_path, pdf_path):
    if not CHROME:
        raise RuntimeError('Chrome/Edge not found for PDF export')
    subprocess.run([CHROME[0], '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
                    f'--print-to-pdf={pdf_path}', 'file:///' + html_path.replace('\\', '/')], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)


def main():
    proc, url = start_server()
    try:
        shoot(url)
    finally:
        proc.kill()
    os.makedirs(OUT, exist_ok=True)
    for role, (title, intro, steps) in GUIDES.items():
        hp = os.path.join(OUT, f'guide_{role}.html')
        open(hp, 'w', encoding='utf-8').write(html_guide(role, title, intro, steps))
        pp = os.path.join(OUT, f'iHIS_guide_{role}.pdf')
        to_pdf(hp, pp); os.remove(hp)
        print('pdf', pp, round(os.path.getsize(pp) / 1e6, 2), 'MB')


if __name__ == '__main__':
    main()
