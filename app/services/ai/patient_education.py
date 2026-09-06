"""Static, clinician-reviewed patient education library.

Local-first content for the patient assistant and the physician's
patient-communication actions. Nothing here diagnoses, prescribes or tells a
patient to change treatment; every entry ends with "discuss with your
physician". Gemini is only used on top of this when available.
"""
import re

LAB_TESTS = {
    'cbc': {'names': ('cbc', 'complete blood count', 'full blood count', 'fbc'),
            'what': 'Counts the cells in your blood: red cells (carry oxygen), white cells (fight infection) and platelets (help clotting).',
            'means': 'Low red cells can point to anaemia; high white cells often accompany infection or inflammation; platelets relate to bleeding and clotting.',
            'important': 'Very low or very high counts usually prompt your physician to look for a cause or repeat the test.'},
    'hemoglobin': {'names': ('hemoglobin', 'haemoglobin', 'hb', 'hgb'),
                   'what': 'The protein in red blood cells that carries oxygen.',
                   'means': 'A low value is called anaemia and can cause tiredness or breathlessness; a high value can happen with dehydration or lung conditions.',
                   'important': 'Small changes are common; a large drop is something your physician will want to explain.'},
    'glucose': {'names': ('glucose', 'blood sugar', 'fasting glucose', 'fbs', 'rbs', 'random glucose'),
                'what': 'The amount of sugar in your blood.',
                'means': 'Fasting values above the reference range may indicate prediabetes or diabetes; very low values cause shakiness, sweating and confusion.',
                'important': 'Timing matters (fasting or not). Trends over time say more than one value.'},
    'hba1c': {'names': ('hba1c', 'a1c', 'glycated hemoglobin', 'glycated haemoglobin'),
              'what': 'Shows your average blood sugar over the last two to three months.',
              'means': 'Higher values mean sugar has been running high on average; it is used to follow diabetes control.',
              'important': 'Your physician sets a personal target; the general goal for many people with diabetes is below 7%.'},
    'creatinine': {'names': ('creatinine', 'egfr', 'kidney function', 'renal function', 'urea', 'bun'),
                   'what': 'Waste products that healthy kidneys clear from the blood; they estimate how well the kidneys filter.',
                   'means': 'A rising creatinine or falling eGFR suggests the kidneys are filtering less well; dehydration and some medicines also affect it.',
                   'important': 'Kidney results often change how medicines are dosed, so keep your physician informed of every medicine you take.'},
    'electrolytes': {'names': ('sodium', 'potassium', 'na', 'k', 'electrolytes', 'chloride', 'calcium', 'magnesium'),
                     'what': 'Minerals in the blood that keep nerves, muscles and the heart working normally.',
                     'means': 'Values outside the range can come from fluid loss, kidney problems or medicines such as diuretics.',
                     'important': 'Potassium that is too high or too low can affect the heart rhythm and is usually rechecked promptly.'},
    'liver': {'names': ('alt', 'ast', 'liver function', 'lft', 'bilirubin', 'alp', 'ggt', 'albumin'),
              'what': 'Enzymes and proteins that reflect how the liver is working.',
              'means': 'Raised enzymes can follow infections, alcohol, fatty liver or medicines; high bilirubin can cause yellowing of the skin.',
              'important': 'Mild, temporary rises are common; persistent or large rises are followed up.'},
    'lipids': {'names': ('cholesterol', 'ldl', 'hdl', 'triglycerides', 'lipid profile', 'lipids'),
               'what': 'Fats in the blood that relate to long-term heart and blood-vessel risk.',
               'means': 'High LDL and triglycerides raise cardiovascular risk; HDL is the protective type.',
               'important': 'Targets depend on your overall risk; lifestyle and sometimes medicines are used to reach them.'},
    'tsh': {'names': ('tsh', 'thyroid', 't4', 't3', 'thyroid function'),
            'what': 'Hormones that show whether the thyroid gland is over- or under-active.',
            'means': 'A high TSH usually means an under-active thyroid; a low TSH an over-active one.',
            'important': 'Thyroid treatment is adjusted slowly and rechecked after several weeks.'},
    'crp': {'names': ('crp', 'c-reactive protein', 'esr', 'inflammation'),
            'what': 'Markers that rise when there is inflammation or infection somewhere in the body.',
            'means': 'They do not say where the inflammation is; they help follow how it responds to treatment.',
            'important': 'A falling value is reassuring; a rising one prompts a closer look.'},
    'urinalysis': {'names': ('urinalysis', 'urine', 'urine test', 'urine culture'),
                   'what': 'Examines the urine for infection, blood, protein and sugar.',
                   'means': 'White cells and bacteria suggest a urinary infection; protein can relate to kidney health.',
                   'important': 'A culture identifies the bacteria and which antibiotic works.'},
    'troponin': {'names': ('troponin', 'cardiac enzymes', 'ck-mb'),
                 'what': 'A protein released when heart muscle is injured.',
                 'means': 'A raised value is taken seriously and usually assessed urgently in hospital.',
                 'important': 'If you have chest pain, seek urgent care regardless of previous results.'},
    'inr': {'names': ('inr', 'pt', 'prothrombin', 'aptt', 'coagulation', 'clotting'),
            'what': 'Measures how quickly your blood clots.',
            'means': 'People on warfarin have a target INR range; values above it raise bleeding risk, below it clotting risk.',
            'important': 'Do not change your blood-thinner dose yourself; contact your clinic if the value is outside your target.'},
    'vitamin_d': {'names': ('vitamin d', '25-oh', 'vitamin b12', 'b12', 'folate', 'iron', 'ferritin'),
                  'what': 'Vitamins and minerals your body needs for bones, blood and nerves.',
                  'means': 'Low levels are common and can cause tiredness or bone pain; they are corrected with supplements.',
                  'important': 'Follow the dose your physician recommends; more is not better.'},
    'xray': {'names': ('x-ray', 'xray', 'radiograph', 'chest x-ray', 'cxr'),
             'what': 'An image that shows bones and the outline of organs such as the lungs and heart.',
             'means': 'The radiologist describes what is seen; "no acute finding" means nothing urgent was found.',
             'important': 'Some findings need follow-up imaging even if you feel well.'},
    'ct': {'names': ('ct', 'ct scan', 'computed tomography'),
           'what': 'A detailed cross-sectional scan that uses X-rays.',
           'means': 'It shows organs, blood vessels and bones in detail; the report lists findings and an impression.',
           'important': 'Your physician explains the impression in the context of your symptoms.'},
    'mri': {'names': ('mri', 'magnetic resonance'),
            'what': 'A scan that uses magnets, not radiation, to show soft tissues in detail.',
            'means': 'The report lists findings and an overall impression.',
            'important': 'Tell staff about any implants before an MRI.'},
    'ultrasound': {'names': ('ultrasound', 'sonography', 'echo', 'echocardiogram', 'doppler'),
                   'what': 'An image made with sound waves; safe and radiation-free.',
                   'means': 'Used for the abdomen, heart, blood vessels and pregnancy.',
                   'important': 'Results are usually discussed at your next visit unless urgent.'},
}

MEDICATIONS = {
    'metformin': {'purpose': 'Lowers blood sugar in type 2 diabetes.',
                  'instructions': 'Take with meals to reduce stomach upset.',
                  'precautions': 'Tell your physician about kidney problems or before contrast scans.'},
    'insulin': {'purpose': 'Replaces the hormone that lowers blood sugar.',
                'instructions': 'Inject as scheduled and rotate sites; keep sugar snacks nearby.',
                'precautions': 'Shakiness, sweating or confusion can mean low sugar: take sugar and contact your clinic.'},
    'amlodipine': {'purpose': 'Lowers blood pressure.', 'instructions': 'Take once daily at the same time.',
                   'precautions': 'Ankle swelling and flushing can occur; do not stop suddenly without advice.'},
    'lisinopril': {'purpose': 'Lowers blood pressure and protects the heart and kidneys.',
                   'instructions': 'Take once daily; blood tests check potassium and kidney function.',
                   'precautions': 'A dry cough is common; swelling of lips or face needs urgent care.'},
    'losartan': {'purpose': 'Lowers blood pressure and protects the kidneys.', 'instructions': 'Take once daily.',
                 'precautions': 'Dizziness when standing; blood tests check potassium.'},
    'atorvastatin': {'purpose': 'Lowers cholesterol to reduce heart-attack and stroke risk.',
                     'instructions': 'Usually once daily, often in the evening.',
                     'precautions': 'Report unexplained muscle pain or dark urine.'},
    'aspirin': {'purpose': 'Low dose thins the blood to prevent clots; higher doses relieve pain.',
                'instructions': 'Take with food.',
                'precautions': 'Increases bleeding risk; tell dentists and surgeons you take it.'},
    'warfarin': {'purpose': 'Prevents blood clots.', 'instructions': 'Take at the same time daily; regular INR blood tests.',
                 'precautions': 'Many medicines and foods interact; report bleeding or black stools immediately.'},
    'clopidogrel': {'purpose': 'Prevents clots after heart procedures or strokes.', 'instructions': 'Once daily.',
                    'precautions': 'Do not stop without asking your cardiologist; bleeding risk.'},
    'omeprazole': {'purpose': 'Reduces stomach acid for reflux or ulcers.', 'instructions': 'Before breakfast.',
                   'precautions': 'Long-term use is reviewed periodically.'},
    'amoxicillin': {'purpose': 'An antibiotic for bacterial infections.',
                    'instructions': 'Finish the whole course even if you feel better.',
                    'precautions': 'Rash, swelling or breathing difficulty may be an allergy: seek care.'},
    'azithromycin': {'purpose': 'An antibiotic for chest, throat and skin infections.',
                     'instructions': 'Usually once daily for a short course.',
                     'precautions': 'Can interact with heart-rhythm medicines.'},
    'ibuprofen': {'purpose': 'Relieves pain and inflammation.', 'instructions': 'Take with food; lowest dose for the shortest time.',
                  'precautions': 'Avoid with kidney disease, stomach ulcers or blood thinners unless advised.'},
    'paracetamol': {'purpose': 'Relieves pain and fever.', 'instructions': 'Do not exceed the daily maximum on the label.',
                    'precautions': 'Check other products for hidden paracetamol.'},
    'acetaminophen': {'purpose': 'Relieves pain and fever.', 'instructions': 'Do not exceed the daily maximum on the label.',
                      'precautions': 'Check other products for hidden acetaminophen.'},
    'salbutamol': {'purpose': 'Opens the airways quickly in asthma or COPD.', 'instructions': 'Use when breathless as directed.',
                   'precautions': 'Needing it more than usual means your control is worse: contact your clinic.'},
    'prednisolone': {'purpose': 'A steroid that reduces inflammation.', 'instructions': 'Take in the morning with food.',
                     'precautions': 'Do not stop long courses abruptly; may raise blood sugar and blood pressure.'},
    'levothyroxine': {'purpose': 'Replaces thyroid hormone.', 'instructions': 'On an empty stomach, same time daily.',
                      'precautions': 'Separate from iron, calcium and antacids by 4 hours.'},
    'furosemide': {'purpose': 'A water tablet that removes excess fluid.', 'instructions': 'Usually in the morning.',
                   'precautions': 'Blood tests check potassium and kidney function; dizziness when standing.'},
    'sertraline': {'purpose': 'Treats depression and anxiety.', 'instructions': 'Takes several weeks to work fully.',
                   'precautions': 'Do not stop suddenly; report worsening mood.'},
    'morphine': {'purpose': 'A strong pain reliever.', 'instructions': 'Exactly as prescribed.',
                 'precautions': 'Drowsiness and constipation; never combine with alcohol or sedatives.'},
    'enoxaparin': {'purpose': 'An injection that prevents blood clots.', 'instructions': 'Injected under the skin as scheduled.',
                   'precautions': 'Report bleeding or unusual bruising.'},
}

TERMS = {
    'hypertension': 'High blood pressure. It usually causes no symptoms but raises the risk of heart and kidney disease over time.',
    'diabetes': 'A condition where blood sugar stays too high because of insulin problems; managed with diet, activity and medicines.',
    'anaemia': 'Too few red blood cells or too little haemoglobin, which can cause tiredness and breathlessness.',
    'anemia': 'Too few red blood cells or too little haemoglobin, which can cause tiredness and breathlessness.',
    'benign': 'Not cancer; does not spread to other parts of the body.',
    'malignant': 'Cancerous; may grow into nearby tissue or spread. Your physician will explain what this means for you.',
    'acute': 'Started recently or suddenly.', 'chronic': 'Long-lasting or recurring.',
    'bilateral': 'On both sides of the body.', 'lesion': 'A general word for an area of abnormal tissue.',
    'edema': 'Swelling caused by fluid in the tissues.', 'oedema': 'Swelling caused by fluid in the tissues.',
    'tachycardia': 'A fast heart rate.', 'bradycardia': 'A slow heart rate.', 'hypoxia': 'Low oxygen in the body.',
    'pneumonia': 'An infection of the lungs.', 'pneumothorax': 'Air leaking into the space around a lung, which can make it collapse; needs prompt care.',
    'embolism': 'A clot or other material blocking a blood vessel.', 'ischemia': 'Reduced blood flow to a tissue.',
    'prognosis': 'The expected course or outcome of a condition.', 'differential': 'The list of possible explanations a physician considers before settling on a diagnosis.',
    'idiopathic': 'Of unknown cause.', 'nil per os': 'Nothing by mouth: do not eat or drink.', 'npo': 'Nothing by mouth: do not eat or drink.',
    'prn': 'Take only when needed.', 'bid': 'Twice a day.', 'tid': 'Three times a day.', 'qid': 'Four times a day.',
    'referral': 'A request for another specialist to see you.', 'biopsy': 'Taking a small tissue sample to examine under a microscope.',
    'sepsis': 'A serious, body-wide response to infection that needs urgent hospital treatment.',
    'fracture': 'A broken bone.', 'osteoarthritis': 'Wear-and-tear joint disease causing pain and stiffness.',
    'gastritis': 'Inflammation of the stomach lining.', 'hyperlipidemia': 'High levels of fats such as cholesterol in the blood.',
    'impression': 'The radiologist\'s or physician\'s overall conclusion after reviewing the findings.',
    'findings': 'What was observed during an examination or on a scan.',
}

APPOINTMENT_PREP = {
    'general': ['Bring your medication list or the boxes', 'Bring your ID and insurance card',
                'Write down your main concern and when it started', 'Note any allergies',
                'Bring previous reports or results from outside the hospital'],
    'lab': ['Ask whether you need to fast (usually 8-12 hours for fasting tests)',
            'Drink water unless told otherwise', 'Take regular medicines unless instructed to hold them'],
    'imaging': ['Remove jewellery and metal objects', 'Tell staff about pregnancy, implants or contrast allergies',
                'Ask about eating and drinking before the scan'],
    'follow_up': ['Bring your home readings (blood pressure, sugar) if you record them',
                  'List any side effects since the last visit', 'Ask about the plan for the next months'],
    'dental': ['Brush before the visit', 'List medicines, especially blood thinners',
               'Mention any pain, sensitivity or bleeding'],
    'physio': ['Wear comfortable clothes', 'Bring any brace or aid you use', 'Note what activities hurt'],
}

QUESTIONS_TO_ASK = [
    'What is the most likely explanation for my symptoms?',
    'What do my results mean for me?',
    'Do I need any further tests?',
    'What are the treatment options and their side effects?',
    'What should I do if symptoms get worse?',
    'When should I come back or contact you?',
]

_WORD = re.compile(r'[a-zA-Z][a-zA-Z0-9\-]+')


def explain_test(name):
    key = (name or '').strip().lower()
    for entry in LAB_TESTS.values():
        if any(alias in key for alias in entry['names']):
            return entry
    return None


def explain_medication(name):
    key = (name or '').strip().lower()
    for generic, entry in MEDICATIONS.items():
        if generic in key:
            return {'generic': generic, **entry}
    return None


def explain_term(term):
    key = (term or '').strip().lower()
    if key in TERMS:
        return TERMS[key]
    for t, text in TERMS.items():
        if t in key or key in t:
            return text
    return None


def find_terms(text):
    """Terms from the glossary that appear in a piece of text."""
    found = {}
    lowered = (text or '').lower()
    for term, explanation in TERMS.items():
        if term in lowered:
            found[term] = explanation
    return found


def preparation_for(visit_type=None, reason=None):
    blob = f'{visit_type or ""} {reason or ""}'.lower().replace('-', ' ').replace('_', ' ')
    key = None
    for k, words in (('lab', ('lab', 'blood test', 'sample')),
                     ('imaging', ('imaging', 'x ray', 'xray', 'ct', 'mri', 'scan', 'ultrasound')),
                     ('dental', ('dental', 'tooth', 'teeth', 'dentist')),
                     ('physio', ('physio', 'rehab', 'therapy')),
                     ('follow_up', ('follow up', 'review', 'control', 'check up'))):
        if any(w in blob for w in words):
            key = k
            break
    return APPOINTMENT_PREP['general'] + (APPOINTMENT_PREP.get(key, []) if key else [])
