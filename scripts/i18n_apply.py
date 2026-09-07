"""Apply Arabic translations to hard-coded English UI strings in templates.

For every template, any visible text node or user-facing attribute whose
whole value equals an English key below is rewritten to the bilingual Jinja
switch iHIS uses everywhere:  ``{{ 'AR' if g.lang == 'ar' else 'EN' }}``.
Units, codes, identifiers and technical names are intentionally not touched.
Idempotent: strings already inside ``{{ … }}`` are skipped.

    python scripts/i18n_apply.py          # apply
    python scripts/i18n_apply.py --dry    # count only
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, 'app', 'templates')

T = {
    # base / navigation
    'Skip to content': 'انتقل إلى المحتوى', 'Main navigation': 'التنقل الرئيسي', 'Account menu': 'قائمة الحساب',
    'Toggle sidebar': 'إظهار/إخفاء القائمة', 'Breadcrumb': 'مسار التنقل', 'Search': 'بحث', 'Tasks': 'المهام',
    'Notifications': 'الإشعارات', 'Preview as role': 'معاينة كدور', 'Close': 'إغلاق', 'AI Clinical Copilot (Ctrl+Shift+A)': 'المساعد السريري الذكي (Ctrl+Shift+A)',
    # lab worklist actions
    'Accept': 'قبول', 'Reject': 'رفض', 'Cancel order': 'إلغاء الطلب', 'Collect Sample': 'سحب العينة', 'Receive at lab': 'استلام في المختبر',
    'Start': 'بدء', 'Enter Result': 'إدخال النتيجة', 'Verify': 'اعتماد', 'View/Correct': 'عرض/تصحيح', 'Finalize': 'إنهاء', 'Reorder': 'إعادة الطلب',
    'CRITICAL': 'حرج', 'Abnormal': 'غير طبيعي', 'CRIT': 'حرج',
    # patient 360 / header
    'Mild': 'خفيف', 'Moderate': 'متوسط', 'Severe': 'شديد', 'Edit': 'تعديل', 'Complete': 'إكمال', 'Blood type': 'فصيلة الدم',
    'Active meds:': 'الأدوية النشطة:', 'Patient 360': 'المريض 360', 'Weight': 'الوزن', 'O2 sat': 'تشبع الأكسجين',
    # radiology
    'AI ANALYSIS': 'تحليل الذكاء الاصطناعي', 'Effective dose': 'الجرعة الفعّالة', 'Administered activity': 'النشاط المُعطى',
    'X-ray': 'أشعة سينية', 'Fluoroscopy': 'تنظير تألقي', 'Mammography': 'تصوير الثدي', 'Nuclear Medicine': 'الطب النووي', 'PET-CT': 'PET-CT',
    'Unknown': 'غير معروف', 'MR Safe': 'آمن للرنين', 'MR Conditional': 'مشروط للرنين', 'MR Unsafe': 'غير آمن للرنين',
    'General': 'عام', 'Contrast': 'صبغة', 'Iodinated': 'يودي', 'Gadolinium': 'جادولينيوم', 'Barium': 'باريوم', 'None': 'لا يوجد', 'Upload': 'رفع',
    'AI Tools': 'أدوات الذكاء',
    # physiotherapy
    'Pain before (0-10)': 'الألم قبل (0-10)', 'Pain after (0-10)': 'الألم بعد (0-10)', 'Modalities': 'الوسائل', 'Adherence': 'الالتزام',
    'No show': 'لم يحضر', 'ROM': 'مدى الحركة', 'reps': 'تكرارات', 'e.g. Strength, Balance, Mobility': 'مثال: قوة، توازن، حركة',
    # nursing
    'AI Medication Review': 'مراجعة الأدوية بالذكاء', 'National Early Warning Score 2': 'مقياس الإنذار المبكر الوطني 2',
    'Overdue': 'متأخر', 'Administered': 'أُعطي', 'Held': 'مُوقَف', 'Refused': 'رفض المريض', 'Missed': 'فائت', 'Discontinued': 'مُلغى',
    'Vital Signs': 'العلامات الحيوية', 'Nursing Notes': 'ملاحظات التمريض', 'Care Plan': 'خطة الرعاية', 'Intake/Output': 'المدخول/المخرجات',
    'Shift': 'الوردية', 'Nurse ID': 'رقم الممرض', 'Given': 'أُعطي', 'Skipped': 'تخطّي',
    # pharmacy
    'Admission': 'دخول', 'Transfer': 'نقل', 'Discharge': 'خروج', 'Ambulatory': 'عيادات خارجية',
    'Minor': 'طفيف', 'Major': 'كبير', 'Contraindicated': 'مضاد استطباب', 'Cancel prescription': 'إلغاء الوصفة', 'Qty': 'الكمية',
    '+/- qty': 'الكمية ±',
    # care / tasks / misc
    'Open': 'مفتوح', 'In Progress': 'قيد التنفيذ', 'Resolved': 'تم الحل', 'Closed': 'مغلق', 'DISMISSED': 'مرفوض',
    'User ID': 'رقم المستخدم', 'This is you': 'هذا أنت', 'new': 'جديد', '-- None --': '-- لا شيء --', 'e.g. Laboratory': 'مثال: المختبر',
    'Key': 'المفتاح', 'Label': 'التسمية', 'Physician, Nurse': 'طبيب، ممرض', 'Search patients': 'بحث عن مرضى',
    'Queue': 'قائمة الانتظار', 'Start consultation and open the chart': 'ابدأ الاستشارة وافتح الملف',
    'Complete visit &amp; bill consultation fee': 'إنهاء الزيارة وفوترة رسوم الاستشارة', 'Mark as no-show': 'تسجيل كغياب',
    'Signed &amp; Locked': 'موقّع ومقفل', 'Draft': 'مسودة', 'AI encounter assistant': 'مساعد الزيارة الذكي', 'AI assistance': 'مساعدة الذكاء',
    'AI ASSISTED': 'بمساعدة الذكاء', 'Original': 'الأصلية', 'Grad-CAM': 'Grad-CAM', 'Review decision': 'قرار المراجعة',
    'Segmentation mask': 'قناع التجزئة', 'Annotated': 'معلّمة', 'Patient image': 'صورة المريض', 'capture': 'التقاط', 'Chest X-ray': 'أشعة الصدر',
    'Dental image': 'صورة أسنان', 'Universal': 'عالمي', 'Palmer': 'بالمر',
    'INV-1001 or patient name': 'INV-1001 أو اسم المريض', 'iHIS Hospital': 'مستشفى iHIS', 'Patient': 'المريض',
    # errors
    'Unauthorized': 'غير مصرّح', 'Your session has expired or you are not signed in. Please log in to continue.': 'انتهت جلستك أو لم تسجّل الدخول. سجّل الدخول للمتابعة.',
    'Sign In': 'تسجيل الدخول', 'Server Error': 'خطأ في الخادم', 'Something went wrong. Our team has been notified.': 'حدث خطأ ما. تم إبلاغ الفريق.',
    'Unprocessable Entity': 'بيانات غير قابلة للمعالجة', 'The request could not be processed because it contained invalid or incomplete data.': 'تعذّرت معالجة الطلب لأنه يحتوي بيانات غير صالحة أو ناقصة.',
    'Page Not Found': 'الصفحة غير موجودة', 'The page you are looking for does not exist.': 'الصفحة التي تبحث عنها غير موجودة.',
    'Access Denied': 'الوصول مرفوض', 'You do not have permission to access this resource.': 'ليست لديك صلاحية الوصول إلى هذا المورد.',
    'Bad Request': 'طلب غير صالح', 'The request could not be understood by the server.': 'لم يتمكن الخادم من فهم الطلب.',
    'Go Back Home': 'العودة للرئيسية',
}

ATTRS = ('placeholder', 'title', 'aria-label', 'alt')


def convert(src):
    n = 0
    def repl_text(m):
        nonlocal n
        txt = m.group(1)
        key = txt.strip()
        if key in T:
            n += 1
            lead = txt[:len(txt) - len(txt.lstrip())]; trail = txt[len(txt.rstrip()):]
            return f">{lead}{{{{ '{T[key]}' if g.lang == 'ar' else '{key}' }}}}{trail}<"
        return m.group(0)
    out = re.sub(r'>([^<>{}]+)<', repl_text, src)
    def repl_attr(m):
        nonlocal n
        attr, val = m.group(1), m.group(2)
        if val in T:
            n += 1
            return f"{attr}=\"{{{{ '{T[val]}' if g.lang == 'ar' else '{val}' }}}}\""
        return m.group(0)
    out = re.sub(r'\b(' + '|'.join(ATTRS) + r')="([^"{}]+)"', repl_attr, out)
    return out, n


def main():
    dry = '--dry' in sys.argv
    total, files = 0, 0
    for dp, _, fs in os.walk(TPL):
        for f in fs:
            if not f.endswith('.html'):
                continue
            p = os.path.join(dp, f)
            src = open(p, encoding='utf-8').read()
            out, n = convert(src)
            if n:
                files += 1; total += n
                if not dry:
                    open(p, 'w', encoding='utf-8').write(out)
    print(f'{"would translate" if dry else "translated"} {total} strings in {files} templates')


if __name__ == '__main__':
    main()
