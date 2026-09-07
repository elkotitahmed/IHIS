"""Local clinical terminology for smart diagnosis entry and autocomplete.

Local-first by design: every lookup here is instant and free. Gemini is only
consulted by the caller *after* local suggestions, and only when enabled.
"""
import io
import os
import re
from collections import Counter

# (code, term, synonyms)
ICD10 = [
    ('I10', 'Essential (primary) hypertension', ('high blood pressure', 'htn')),
    ('E11.9', 'Type 2 diabetes mellitus without complications', ('t2dm', 'diabetes type 2', 'niddm')),
    ('E10.9', 'Type 1 diabetes mellitus without complications', ('t1dm', 'diabetes type 1')),
    ('E11.65', 'Type 2 diabetes mellitus with hyperglycemia', ('uncontrolled diabetes',)),
    ('E78.5', 'Hyperlipidemia, unspecified', ('high cholesterol', 'dyslipidemia')),
    ('E66.9', 'Obesity, unspecified', ()),
    ('E03.9', 'Hypothyroidism, unspecified', ('underactive thyroid',)),
    ('E05.90', 'Thyrotoxicosis, unspecified', ('hyperthyroidism', 'overactive thyroid')),
    ('D50.9', 'Iron deficiency anemia, unspecified', ('ida', 'anaemia')),
    ('D64.9', 'Anemia, unspecified', ('anaemia',)),
    ('J06.9', 'Acute upper respiratory infection, unspecified', ('uri', 'urti', 'common cold')),
    ('J18.9', 'Pneumonia, unspecified organism', ('chest infection', 'cap')),
    ('J45.909', 'Unspecified asthma, uncomplicated', ('asthma',)),
    ('J44.9', 'Chronic obstructive pulmonary disease, unspecified', ('copd', 'emphysema')),
    ('J02.9', 'Acute pharyngitis, unspecified', ('sore throat',)),
    ('J01.90', 'Acute sinusitis, unspecified', ('sinusitis',)),
    ('J20.9', 'Acute bronchitis, unspecified', ('bronchitis',)),
    ('I25.10', 'Atherosclerotic heart disease of native coronary artery without angina', ('cad', 'ihd', 'coronary artery disease')),
    ('I21.9', 'Acute myocardial infarction, unspecified', ('heart attack', 'mi', 'stemi', 'nstemi')),
    ('I20.9', 'Angina pectoris, unspecified', ('angina', 'chest pain cardiac')),
    ('I50.9', 'Heart failure, unspecified', ('chf', 'congestive heart failure', 'hf')),
    ('I48.91', 'Unspecified atrial fibrillation', ('af', 'afib')),
    ('I63.9', 'Cerebral infarction, unspecified', ('stroke', 'cva', 'ischemic stroke')),
    ('G45.9', 'Transient cerebral ischemic attack, unspecified', ('tia', 'mini stroke')),
    ('I26.99', 'Other pulmonary embolism without acute cor pulmonale', ('pe', 'pulmonary embolism')),
    ('I82.409', 'Acute embolism and thrombosis of unspecified deep veins of unspecified lower extremity', ('dvt', 'deep vein thrombosis')),
    ('K21.9', 'Gastro-esophageal reflux disease without esophagitis', ('gerd', 'reflux', 'heartburn')),
    ('K29.70', 'Gastritis, unspecified, without bleeding', ('gastritis',)),
    ('K35.80', 'Unspecified acute appendicitis', ('appendicitis',)),
    ('K80.20', 'Calculus of gallbladder without cholecystitis without obstruction', ('gallstones', 'cholelithiasis')),
    ('K59.00', 'Constipation, unspecified', ()),
    ('A09', 'Infectious gastroenteritis and colitis, unspecified', ('gastroenteritis', 'diarrhea', 'diarrhoea', 'food poisoning')),
    ('N39.0', 'Urinary tract infection, site not specified', ('uti', 'cystitis')),
    ('N18.9', 'Chronic kidney disease, unspecified', ('ckd', 'renal failure chronic')),
    ('N17.9', 'Acute kidney failure, unspecified', ('aki', 'acute kidney injury')),
    ('N20.0', 'Calculus of kidney', ('kidney stone', 'renal stone', 'nephrolithiasis')),
    ('M54.5', 'Low back pain', ('lbp', 'backache', 'lumbago')),
    ('M54.2', 'Cervicalgia', ('neck pain',)),
    ('M17.9', 'Osteoarthritis of knee, unspecified', ('knee oa', 'knee arthritis')),
    ('M19.90', 'Unspecified osteoarthritis, unspecified site', ('osteoarthritis', 'oa')),
    ('M06.9', 'Rheumatoid arthritis, unspecified', ('ra',)),
    ('M81.0', 'Age-related osteoporosis without current pathological fracture', ('osteoporosis',)),
    ('M79.1', 'Myalgia', ('muscle pain',)),
    ('M25.50', 'Pain in unspecified joint', ('joint pain', 'arthralgia')),
    ('S52.509A', 'Unspecified fracture of the lower end of unspecified radius, initial encounter', ('wrist fracture', 'colles')),
    ('S72.009A', 'Fracture of unspecified part of neck of unspecified femur, initial encounter', ('hip fracture', 'nof')),
    ('S82.90XA', 'Unspecified fracture of unspecified lower leg, initial encounter', ('leg fracture', 'tibia fracture')),
    ('S93.409A', 'Sprain of unspecified ligament of unspecified ankle, initial encounter', ('ankle sprain',)),
    ('T14.90XA', 'Injury, unspecified, initial encounter', ('trauma', 'injury')),
    ('L03.90', 'Cellulitis, unspecified', ()),
    ('L20.9', 'Atopic dermatitis, unspecified', ('eczema',)),
    ('L40.9', 'Psoriasis, unspecified', ()),
    ('L70.9', 'Acne, unspecified', ()),
    ('D22.9', 'Melanocytic nevi, unspecified', ('mole', 'nevus', 'naevus')),
    ('C43.9', 'Malignant melanoma of skin, unspecified', ('melanoma',)),
    ('L98.9', 'Disorder of the skin and subcutaneous tissue, unspecified', ('skin lesion', 'rash')),
    ('B34.9', 'Viral infection, unspecified', ('viral illness',)),
    ('R50.9', 'Fever, unspecified', ('pyrexia',)),
    ('R51.9', 'Headache, unspecified', ()),
    ('G43.909', 'Migraine, unspecified, not intractable', ('migraine',)),
    ('R07.9', 'Chest pain, unspecified', ()),
    ('R10.9', 'Unspecified abdominal pain', ('abdominal pain', 'stomach ache')),
    ('R06.02', 'Shortness of breath', ('dyspnea', 'dyspnoea', 'sob')),
    ('R05.9', 'Cough, unspecified', ()),
    ('R42', 'Dizziness and giddiness', ('vertigo', 'dizzy')),
    ('R53.83', 'Other fatigue', ('tiredness', 'fatigue')),
    ('R11.2', 'Nausea with vomiting, unspecified', ('vomiting', 'nausea')),
    ('R73.03', 'Prediabetes', ('impaired fasting glucose',)),
    ('F32.A', 'Depression, unspecified', ('depressive disorder',)),
    ('F41.1', 'Generalized anxiety disorder', ('anxiety', 'gad')),
    ('F41.9', 'Anxiety disorder, unspecified', ()),
    ('G47.00', 'Insomnia, unspecified', ()),
    ('H10.9', 'Unspecified conjunctivitis', ('pink eye', 'red eye')),
    ('H66.90', 'Otitis media, unspecified, unspecified ear', ('ear infection',)),
    ('H81.10', 'Benign paroxysmal vertigo, unspecified ear', ('bppv',)),
    ('K02.9', 'Dental caries, unspecified', ('caries', 'tooth decay', 'cavity')),
    ('K04.7', 'Periapical abscess without sinus', ('dental abscess', 'tooth abscess')),
    ('K05.10', 'Chronic gingivitis, plaque induced', ('gingivitis',)),
    ('K05.30', 'Chronic periodontitis, unspecified', ('periodontitis', 'gum disease')),
    ('K08.9', 'Disorder of teeth and supporting structures, unspecified', ('dental problem',)),
    ('O80', 'Encounter for full-term uncomplicated delivery', ('normal delivery',)),
    ('Z00.00', 'Encounter for general adult medical examination without abnormal findings', ('check-up', 'annual exam', 'physical')),
    ('Z23', 'Encounter for immunization', ('vaccination', 'vaccine')),
    ('Z09', 'Encounter for follow-up examination after completed treatment', ('follow-up',)),
    ('Z79.4', 'Long term (current) use of insulin', ()),
    ('Z86.73', 'Personal history of TIA and cerebral infarction without residual deficits', ()),
    ('A41.9', 'Sepsis, unspecified organism', ('sepsis', 'septicemia')),
    ('J93.9', 'Pneumothorax, unspecified', ()),
    ('I71.00', 'Dissection of unspecified site of aorta', ('aortic dissection',)),
    ('I61.9', 'Nontraumatic intracerebral hemorrhage, unspecified', ('brain bleed', 'ich')),
    ('K56.609', 'Unspecified intestinal obstruction, unspecified as to partial versus complete obstruction', ('bowel obstruction', 'ileus')),
    ('E86.0', 'Dehydration', ()),
    ('E87.6', 'Hypokalemia', ('low potassium',)),
    ('E87.5', 'Hyperkalemia', ('high potassium',)),
    ('E87.1', 'Hypo-osmolality and hyponatremia', ('low sodium', 'hyponatremia')),
    ('R73.9', 'Hyperglycemia, unspecified', ('high blood sugar',)),
    ('E16.2', 'Hypoglycemia, unspecified', ('low blood sugar',)),
]

PHRASES = {
    'hpi': ['Patient presents with', 'Onset was sudden', 'Onset was gradual over', 'Symptoms started', 'days ago',
            'associated with', 'no associated', 'aggravated by', 'relieved by', 'denies chest pain, dyspnea or palpitations',
            'denies fever, chills or night sweats', 'no history of similar episodes', 'similar episode previously',
            'no recent travel or sick contacts', 'compliant with medications', 'non-compliant with medications',
            'pain rated', 'out of 10', 'radiating to', 'worse at night', 'improving with rest', 'progressively worsening'],
    'exam': ['Alert and oriented, no acute distress', 'Vital signs within normal limits', 'Afebrile', 'Chest clear to auscultation bilaterally',
             'Heart sounds normal, no murmurs', 'Abdomen soft, non-tender, no organomegaly', 'No peripheral edema', 'Neurological examination unremarkable',
             'Mild tenderness on palpation of', 'Range of motion limited by pain', 'Skin warm and dry', 'No lymphadenopathy',
             'Pharynx erythematous without exudate', 'Bilateral crepitations at', 'Wheeze on expiration', 'Capillary refill under 2 seconds',
             'Pupils equal and reactive to light', 'No focal neurological deficit', 'Oral mucosa moist', 'Ears: tympanic membranes intact'],
    'assessment': ['Clinical picture consistent with', 'Most likely', 'Differential includes', 'Rule out', 'Stable chronic', 'Poorly controlled',
                   'Well controlled', 'Acute exacerbation of', 'No red flags identified', 'Requires further evaluation for', 'Working diagnosis:'],
    'plan': ['Start', 'Continue current medications', 'Increase dose of', 'Reduce dose of', 'Order CBC, renal profile and CRP', 'Order chest X-ray',
             'Refer to', 'Review in 1 week', 'Review in 2 weeks', 'Review in 4 weeks with results', 'Return precautions explained', 'Patient education provided',
             'Lifestyle advice: diet, exercise, smoking cessation', 'Monitor blood pressure at home', 'Monitor blood glucose', 'Sick leave for', 'days',
             'Admit for observation', 'Discharge with follow-up', 'Safety-netting advice given', 'Consider'],
    'diagnosis': [t for _, t, _ in ICD10],
    'referral': ['Kindly assess and manage', 'Thank you for seeing this patient with', 'Referred for specialist opinion regarding',
                 'Relevant history:', 'Current medications:', 'Allergies:', 'Specific question:', 'Urgent review requested for',
                 'Routine review requested for', 'Investigations to date:', 'Please advise on further management'],
    'discharge': ['Admitted with', 'Hospital course was uncomplicated', 'Treated with intravenous', 'Improved clinically', 'Discharged in stable condition',
                  'Discharge medications:', 'Follow-up in clinic in', 'Return to emergency department if', 'Wound care instructions given',
                  'Activity as tolerated', 'Diet: regular', 'Pending results at discharge:', 'Outpatient investigations arranged:'],
    'nursing': ['Patient resting comfortably', 'Vital signs stable', 'Pain managed with prescribed analgesia', 'Tolerating oral intake', 'Ambulating independently',
                'Requires assistance with mobility', 'Wound dressing dry and intact', 'IV site clean, no signs of infection', 'Medications administered as charted',
                'Patient educated on', 'Family updated', 'Fall precautions in place', 'Intake and output recorded', 'No complaints voiced', 'Pressure areas intact',
                'Oxygen via nasal cannula at', 'Handed over to', 'Escalated to physician regarding'],
    'physio': ['Range of motion improved', 'Pain reduced from', 'Gait pattern:', 'Muscle strength grade', 'Performed exercises as prescribed', 'Home exercise program reviewed',
               'Balance exercises progressed', 'Tolerated session well', 'Patient reports', 'Plan: continue', 'Progress to', 'Goals reviewed with patient'],
    'dental': ['Caries noted on', 'Periodontal pockets of', 'Gingival inflammation present', 'Restoration intact', 'Occlusion normal', 'Radiograph shows',
               'Tooth tender to percussion', 'Mobility grade', 'Plaque and calculus present', 'Oral hygiene instruction given', 'Treatment plan discussed and consented',
               'Local anaesthesia administered', 'Extraction completed without complication', 'Composite restoration placed', 'Review in 6 months'],
}

_TOKEN = re.compile(r'[a-zA-Z0-9\-\.]+')


_ICD_FULL = None          # [(code, description, description_lower, words)] from app/data/icd10cm_2026.tsv
_ICD_FULL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'icd10cm_2026.tsv')


def _icd_full():
    """Official ICD-10-CM FY2026 list (public domain), loaded once per process."""
    global _ICD_FULL
    if _ICD_FULL is None:
        rows = []
        if os.path.isfile(_ICD_FULL_FILE):
            with io.open(_ICD_FULL_FILE, encoding='utf-8') as f:
                for ln in f:
                    if ln.startswith('#'):
                        continue
                    code, _, desc = ln.rstrip('\n').partition('\t')
                    if code and desc:
                        low = desc.lower()
                        rows.append((code, desc, low, [w.strip('(),;') for w in low.split()]))
        _ICD_FULL = rows
    return _ICD_FULL


def icd_count():
    return len(_icd_full())


def search_icd(q, limit=8):
    q = (q or '').strip().lower()
    if len(q) < 2:
        return []
    curated = _search_icd_curated(q, limit)
    if len(curated) >= limit:
        return curated
    seen = {r['code'] for r in curated}
    qc = q.upper().replace(' ', '')
    starts, words, contains = [], [], []
    for code, desc, low, ws in _icd_full():
        if code in seen:
            continue
        if code.startswith(qc) or low.startswith(q):
            starts.append((code, desc))
        elif any(w.startswith(q) for w in ws):
            words.append((code, desc))
        elif len(q) >= 4 and q in low:
            contains.append((code, desc))
        if len(starts) >= limit * 3:
            break
    extra = [{'code': c, 'term': t} for c, t in (starts + words + contains)]
    return (curated + extra)[:limit]


def _search_icd_curated(q, limit=8):
    starts, word_starts, contains = [], [], []
    for code, term, syns in ICD10:
        hay = term.lower()
        words = [w.strip('(),') for w in hay.split()] + [w for s in syns for w in s.split()]
        if hay.startswith(q) or code.lower().startswith(q) or any(s.startswith(q) for s in syns):
            starts.append((code, term))
        elif any(w.startswith(q) for w in words):
            word_starts.append((code, term))       # "hyper" -> Essential hypertension
        elif q in hay or any(q in s for s in syns):
            contains.append((code, term))
    # list order encodes clinical frequency (I10 before rarer hyper- codes)
    out = sorted(starts + word_starts, key=lambda ct: [c for c, _, _ in ICD10].index(ct[0])) + contains
    return [{'code': c, 'term': t} for c, t in out[:limit]]


def suggest_phrases(field, q, limit=8):
    field = (field or '').lower()
    bank = PHRASES.get(field) or PHRASES['hpi']
    q = (q or '').strip().lower()
    if not q:
        return bank[:limit]
    starts = [p for p in bank if p.lower().startswith(q)]
    words = [p for p in bank if q in p.lower() and p not in starts]
    return (starts + words)[:limit]


def user_recent_diagnoses(user_id, limit=8):
    """The clinician's own recently used diagnoses (recent + favourites)."""
    from app.models import Diagnosis, Doctor, MedicalRecord
    doc = Doctor.query.filter_by(user_id=user_id).first()
    if doc is None:
        return [], []
    rows = (Diagnosis.query.filter_by(doctor_id=doc.id)
            .order_by(Diagnosis.date_diagnosed.desc()).limit(200).all())
    texts = [(d.icd10_code, (d.description or '').strip()) for d in rows if d.description]
    recs = (MedicalRecord.query.filter_by(doctor_id=doc.id)
            .order_by(MedicalRecord.visit_date.desc()).limit(200).all())
    texts += [(None, (r.diagnosis or '').strip()) for r in recs if r.diagnosis]
    recent, seen = [], set()
    for code, text in texts:
        key = text.lower()
        if key and key not in seen:
            seen.add(key)
            recent.append({'code': code, 'term': text})
        if len(recent) >= limit:
            break
    counts = Counter((t or '').lower() for _, t in texts if t)
    favourites = [{'code': next((c for c, t in texts if (t or '').lower() == k and c), None), 'term': k.capitalize()}
                  for k, n in counts.most_common(limit) if n >= 2]
    return recent, favourites


def diagnosis_lookup(q, user_id=None):
    recent, favs = user_recent_diagnoses(user_id) if user_id else ([], [])
    q_l = (q or '').strip().lower()
    return {
        'terminology': search_icd(q),
        'recent': [r for r in recent if not q_l or q_l in r['term'].lower()][:5],
        'favorites': [f for f in favs if not q_l or q_l in f['term'].lower()][:5],
    }
