"""Comprehensive sandbox-ready seed data for iHIS.

Creates the 5 reference patients described in the project spec, each one
designed to exercise a distinct set of clinical & administrative scenarios so
that every button in the system can be tested against real, related records
(no empty pages, no fabricated dashboard numbers).

Development only — refuses to run against a server (non-SQLite) database.

Run:
    python seed.py                 # roles + catalogue + original demo account
    python seed_patients.py        # the 5 sandbox patients on top

All created accounts use the password:  123456
"""
import sys
from datetime import datetime, timedelta, date

from app import create_app, db
from seed import seed_roles_and_permissions
from app.models import (
    User, Role, Department, Specialty, Doctor, Patient,
    ImagingType, LabTestCatalog, Medication, PharmacyInventory,
    DentalSpecialty, Dentist, PhysicalTherapist,
    MedicalRecord, Diagnosis, Prescription, PrescriptionItem, LabOrder, LabResult,
    RadiologyOrder, RadiologyReport, Appointment, VitalSign, NursingNote, CarePlan,
    DentalChart, DentalTreatmentPlan, DentalProcedure, DentalImage, OrthodonticCase,
    TherapyAssessment, TherapyPlan, TherapySession, FunctionalOutcome,
    Notification, ServiceCatalog, Bill, BillItem, Payment,
    Ward, Bed, Admission, TimelineEvent, ClinicalAlert, Allergy, Problem,
    ImmunizationRecord, FollowUp, NursingRiskAssessment,
    MedicationReconciliation, ReconciliationDiscrepancy, PharmacyIntervention,
    Message, Task, CareTeam, CareTeamMember, MultidisciplinaryCase, Referral,
    PatientImagingSafetyProfile, ImagingDoseRecord, RadiologyReportVersion,
    RehabilitationProgress,
)
from app.services.billing import ensure_bill_for_consultation, ensure_bill_for_lab
from app.services.billing import ensure_bill_for_radiology, ensure_bill_for_dental
from app.services.billing import ensure_bill_for_physio, ensure_bill_for_pharmacy

PASSWORD = '123456'
NOW = datetime.utcnow()


def _date(offset_days=0):
    return (date.today() + timedelta(days=offset_days))


def _dt(offset_days=0, hour=9, minute=0):
    return datetime.combine(_date(offset_days), datetime.min.time()) \
        + timedelta(hours=hour, minutes=minute)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _get_user(username):
    return User.query.filter_by(username=username).first()


def _get_doctor(username):
    u = _get_user(username)
    if u is None:
        return None
    return Doctor.query.filter_by(user_id=u.id).first()


def _get_patient_by_mrn(mrn):
    return Patient.query.filter_by(mrn=mrn).first()


def _role(name):
    return Role.query.filter_by(name=name).first()


def _dept(name):
    return Department.query.filter_by(name=name).first()


def _spec(name):
    return Specialty.query.filter_by(name=name).first()


def _med(generic):
    return Medication.query.filter_by(generic_name=generic).first()


def _lab_test(test_name):
    return LabTestCatalog.query.filter_by(test_name=test_name).first()


def _imaging(name):
    return ImagingType.query.filter_by(name=name).first()


def _service(name):
    return ServiceCatalog.query.filter_by(name=name).first()


def _create_patient_user(username, email, full_name, mrn, **profile):
    """Create-or-get a Patient portal user + a Patient record with an MRN."""
    user = _get_user(username)
    if user is None:
        user = User(username=username, email=email, full_name=full_name,
                    user_type='patient')
        r = _role('Patient')
        if r and r not in user.roles:
            user.roles.append(r)
        db.session.add(user)
    user.set_password(PASSWORD)
    db.session.flush()
    pat = _get_patient_by_mrn(mrn)
    if pat is None:
        if not profile.get('user_id'):
            profile['user_id'] = user.id
        profile['mrn'] = mrn
        pat = Patient(**profile)
        db.session.add(pat)
        db.session.flush()
    return user, pat


def _create_staff_user(username, email, full_name, user_type, role_name,
                       department=None, specialty=None):
    """Create-or-get a staff user (doctor/dentist/therapist/nurse/...)."""
    from app.models import User as U
    u = _get_user(username)
    if u is None:
        u = U(username=username, email=email, full_name=full_name,
              user_type=user_type)
        r = _role(role_name)
        if r and r not in u.roles:
            u.roles.append(r)
        db.session.add(u)
    u.set_password(PASSWORD)
    db.session.flush()
    if department:
        d = _dept(department)
        if d:
            u.department_id = d.id
    db.session.flush()
    return u


def _create_doctor(username, email, full_name, specialty_name, fee=300.0):
    u = _create_staff_user(username, email, full_name, 'doctor', 'Doctor',
                           department=None, specialty=specialty_name)
    doc = Doctor.query.filter_by(user_id=u.id).first()
    if doc is None:
        spec = _spec(specialty_name)
        doc = Doctor(user_id=u.id, specialty_id=spec.id if spec else None,
                     license_number=f'LIC-{username}',
                     years_of_experience=8, consultation_fee=fee)
        db.session.add(doc)
        db.session.flush()
    return doc


def _create_dentist(username, email, full_name, dental_spec_name):
    u = _create_staff_user(username, email, full_name, 'dentist', 'Dentist',
                           department='Dentistry')
    dent = Dentist.query.filter_by(user_id=u.id).first()
    if dent is None:
        dspec = DentalSpecialty.query.filter_by(name=dental_spec_name).first()
        dent = Dentist(user_id=u.id,
                       dental_specialty_id=dspec.id if dspec else None,
                       license_number=f'DEN-{username}',
                       years_of_experience=7)
        db.session.add(dent)
        db.session.flush()
    return dent


def _create_therapist(username, email, full_name, specialization):
    u = _create_staff_user(username, email, full_name, 'physiotherapist',
                           'Physiotherapist', department='Rehabilitation')
    th = PhysicalTherapist.query.filter_by(user_id=u.id).first()
    if th is None:
        th = PhysicalTherapist(user_id=u.id, specialization=specialization,
                               license_number=f'PT-{username}',
                               years_of_experience=8)
        db.session.add(th)
        db.session.flush()
    return th


def _add_allergy(patient, substance, reaction, severity='Severe', verified=True,
                 onset='Adult', recorder_username='admin'):
    if Allergy.query.filter_by(patient_id=patient.id, substance=substance).first():
        return
    rec = _get_user(recorder_username)
    db.session.add(Allergy(patient_id=patient.id, substance=substance,
                           reaction=reaction, severity=severity, verified=verified,
                           onset=onset, recorded_by=rec.id if rec else None))


def _add_problem(patient, icd10, description, onset, severity='Moderate',
                 recorder_username='admin'):
    if Problem.query.filter_by(patient_id=patient.id, description=description).first():
        return
    rec = _get_user(recorder_username)
    db.session.add(Problem(patient_id=patient.id, icd10_code=icd10,
                           description=description, onset=onset, status='Active',
                           severity=severity,
                           recorded_by=rec.id if rec else None))


def _add_diagnosis(patient, doctor, icd10, description, when, is_primary=True):
    if Diagnosis.query.filter_by(patient_id=patient.id, icd10_code=icd10).first():
        return None
    diag = Diagnosis(patient_id=patient.id, doctor_id=doctor.id, icd10_code=icd10,
                     description=description, is_primary=is_primary,
                     date_diagnosed=when)
    db.session.add(diag)
    return diag


def _add_record(patient, doctor, diagnosis, plan, notes, when, status='Signed'):
    mr = MedicalRecord(patient_id=patient.id, doctor_id=doctor.id,
                       diagnosis=diagnosis, treatment_plan=plan,
                       clinical_notes=notes, visit_date=when, status=status)
    db.session.add(mr)
    return mr


def _add_vital(patient, nurse_user, **vals):
    vital = VitalSign(patient_id=patient.id,
                      nurse_id=nurse_user.id if nurse_user else None)
    for k, v in vals.items():
        setattr(vital, k, v)
    db.session.add(vital)
    return vital


def _add_prescription(patient, doctor, items, when, status='Active', refills=0):
    rx = Prescription(patient_id=patient.id, doctor_id=doctor.id, status=status,
                      refills=refills, prescribed_date=when)
    db.session.add(rx)
    db.session.flush()
    for it in items:
        med = _med(it['generic'])
        db.session.add(PrescriptionItem(prescription_id=rx.id,
                                        medication_id=med.id if med else None,
                                        dosage=it.get('dosage'),
                                        frequency=it.get('frequency'),
                                        duration=it.get('duration'),
                                        instructions=it.get('instructions'),
                                        quantity=it.get('quantity', 1)))
    db.session.flush()
    return rx


def _add_appointment(patient, doctor, when, status='Scheduled', reason=None,
                     priority='Normal', duration=30, visit_type='Scheduled',
                     creator_username='reception', queue_number=None,
                     checked_in_at=None):
    if Appointment.query.filter_by(patient_id=patient.id, doctor_id=doctor.id,
                                   scheduled_at=when).first():
        return None
    creator = _get_user(creator_username)
    appt = Appointment(patient_id=patient.id, doctor_id=doctor.id,
                       scheduled_at=when, duration_minutes=duration, status=status,
                       reason=reason, priority=priority, visit_type=visit_type,
                       queue_number=queue_number, checked_in_at=checked_in_at,
                       created_by=creator.id if creator else None)
    db.session.add(appt)
    db.session.flush()
    return appt


def _add_lab_order(patient, doctor, test_name, when, status='Pending',
                   priority='Normal', specimen='Blood'):
    test = _lab_test(test_name)
    order = LabOrder(patient_id=patient.id, doctor_id=doctor.id,
                     test_id=test.id if test else None, status=status,
                     priority=priority, order_date=when,
                     specimen_type=specimen)
    db.session.add(order)
    db.session.flush()
    return order


def _add_lab_result(order, value, is_abnormal, is_critical, unit, when,
                    status='Verified', notes=None, lab_user_username='lab_tech'):
    result = LabResult(order_id=order.id, result_value=str(value),
                       result_unit=unit, is_abnormal=is_abnormal,
                       is_critical=is_critical, result_date=when, status=status,
                       result_notes=notes)
    lab = _get_user(lab_user_username)
    if lab:
        result.validated_by = lab.id
        result.created_by = lab.id
    db.session.add(result)
    return result


def _add_radiology_order(patient, doctor, imaging_name, when, status='Pending',
                         priority='Normal', scheduled_at=None, notes=None):
    img = _imaging(imaging_name)
    order = RadiologyOrder(patient_id=patient.id, doctor_id=doctor.id,
                           imaging_type_id=img.id if img else None, status=status,
                           priority=priority, order_date=when,
                           scheduled_at=scheduled_at, notes=notes)
    db.session.add(order)
    db.session.flush()
    return order


def _add_radiology_report(order, findings, impression, recommendation,
                          reporter_username='radio', status='Draft', when=None):
    rep = RadiologyReport(order_id=order.id, findings=findings,
                          impression=impression, recommendation=recommendation,
                          report_date=when or NOW, status=status)
    rep_user = _get_user(reporter_username)
    if rep_user:
        rep.reported_by = rep_user.id
    db.session.add(rep)
    db.session.flush()
    return rep


def _add_timeline(patient, event_type, title, description, occurred_at,
                  creator_username='admin', source_type=None, source_id=None,
                  department=None):
    creator = _get_user(creator_username)
    ev = TimelineEvent(patient_id=patient.id, event_type=event_type, title=title,
                       description=description, occurred_at=occurred_at,
                       source_type=source_type, source_id=source_id,
                       department=department,
                       created_by=creator.id if creator else None)
    db.session.add(ev)
    return ev


def _add_alert(patient, alert_type, severity, title, message, status='OPEN',
               source_type=None, source_id=None):
    al = ClinicalAlert(patient_id=patient.id, alert_type=alert_type,
                       severity=severity, title=title, message=message,
                       status=status, source_type=source_type, source_id=source_id)
    db.session.add(al)
    return al


def _add_careteam(patient, name, members):
    """members: list of (username, role)."""
    ct = CareTeam(patient_id=patient.id, name=name)
    db.session.add(ct)
    db.session.flush()
    for uname, role in members:
        u = _get_user(uname)
        if u:
            db.session.add(CareTeamMember(team_id=ct.id, user_id=u.id, role=role))
    db.session.flush()
    return ct


def _add_mdcase(patient, title, description, status='Open', creator_username='admin'):
    if MultidisciplinaryCase.query.filter_by(patient_id=patient.id, title=title).first():
        return None
    return MultidisciplinaryCase(patient_id=patient.id, title=title,
                                 description=description, status=status)


def _add_message(sender_username, receiver_username, subject, body, when, is_read=False):
    s = _get_user(sender_username)
    r = _get_user(receiver_username)
    if s and r and Message.query.filter_by(sender_id=s.id, receiver_id=r.id,
                                           subject=subject).first():
        return None
    return Message(sender_id=s.id if s else None,
                   receiver_id=r.id if r else None, subject=subject, body=body,
                   is_read=is_read, sent_at=when)


def _add_followup(patient, doctor, when, reason, status='Scheduled',
                  creator_username='doctor'):
    creator = _get_user(creator_username)
    return FollowUp(patient_id=patient.id, provider_id=doctor.id,
                    scheduled_for=when, reason=reason, status=status,
                    created_by=creator.id if creator else None)


def _pay_bill(patient, amount, method='Cash', reference=None, cashier_username='cashier'):
    """Fully or partially pay the given amount against the patient's oldest unpaid bill (for demo numbering)."""
    bill = (Bill.query.filter_by(patient_id=patient.id)
            .filter(Bill.status != 'Paid').order_by(Bill.issued_at.asc()).first())
    if bill is None:
        return None
    cashier = _get_user(cashier_username)
    pay = Payment(bill_id=bill.id, amount=amount, method=method, reference=reference,
                  received_by=cashier.id if cashier else None,
                  receipt_no=f'RCP-{bill.id}-{amount:.0f}')
    db.session.add(pay)
    db.session.flush()
    if bill.paid_amount() >= bill.total() - 0.01:
        bill.status = 'Paid'
    elif bill.paid_amount() > 0:
        bill.status = 'PartiallyPaid'
    return pay


# --------------------------------------------------------------------------- #
# Patient 1 - Ahmed (Emergency cardiac + critical lab + drug interaction)
# --------------------------------------------------------------------------- #
def _seed_ahmed():
    mrn = 'P10001'
    user, patient = _create_patient_user(
        'patient_ahmed', 'ahmed.sayed@ihis.com', 'Ahmed Mohamed El-Sayed', mrn,
        date_of_birth=_date(-(58 * 365)), gender='Male', phone='0501234567',
        address='Dubai, UAE', blood_type='O+',
        chronic_diseases='Hypertension')
    if MedicalRecord.query.filter_by(patient_id=patient.id).count() > 0:
        print('  [P1] Ahmed already seeded, skipping.')
        return

    doc = _create_doctor('dr_khaled', 'dr.khaled@ihis.com', 'Dr. Khaled',
                         'Cardiology', fee=300.0)
    nurse = _get_user('nurse') or _create_staff_user(
        'nurse', 'nurse@ihis.com', 'Nurse Nour', 'nurse', 'Nurse',
        department='Nursing')

    # Problems / allergy
    _add_problem(patient, 'I10', 'Essential hypertension', _date(-(5 * 365)),
                 severity='Moderate', recorder_username='admin')

    # Appointment today emergency
    when = _dt(0, 10, 0)
    appt = _add_appointment(patient, doc, when, status='CheckedIn',
                            reason='Chest pain, shortness of breath',
                            priority='Urgent', visit_type='WalkIn',
                            queue_number=1, checked_in_at=_dt(0, 9, 30))

    # Vitals at reception (abnormal)
    _add_vital(patient, None,
               blood_pressure_systolic=165, blood_pressure_diastolic=105,
               heart_rate=110, temperature=37.2, respiratory_rate=20,
               oxygen_saturation=94, pain_score=6, recorded_at=_dt(0, 9, 35))

    # Diagnosis
    _add_diagnosis(patient, doc, 'I20.9', 'Unstable angina', when, is_primary=True)

    # Medical record
    _add_record(patient, doc,
                'I20.9 - Unstable angina',
                'Aspirin 300mg daily, Atorvastatin 40mg daily, sublingual nitroglycerin PRN, urgent cardiac review.',
                'Patient presented with crushing chest pain radiating to left arm, diaphoresis. BP 165/105, HR 110, SpO2 94%. Started on antiplatelet + statin. Troponin ordered urgently.',
                when, status='Signed')

    # Prescription
    _add_prescription(patient, doc, [
        {'generic': 'Aspirin', 'dosage': '300 mg', 'frequency': 'Once daily',
         'duration': '30 days', 'instructions': 'Take after food', 'quantity': 30},
        {'generic': 'Atorvastatin', 'dosage': '40 mg', 'frequency': 'Once daily',
         'duration': '30 days', 'instructions': 'Take at night', 'quantity': 30},
    ], when)

    # Critical lab orders: Troponin I (critical) + Creatinine
    tropo = _lab_test('Cardiac Troponin I') or _lab_test('HbA1c')
    if tropo is None:
        tropo = LabTestCatalog(test_name='Cardiac Troponin I', category='Cardiology',
                               normal_range='<0.04', unit='ng/mL', price=150.0)
        db.session.add(tropo)
        db.session.flush()
    tro = LabOrder(patient_id=patient.id, doctor_id=doc.id, test_id=tropo.id,
                   status='Finalized', priority='Urgent', order_date=_dt(0, 10, 20),
                   specimen_type='Blood')
    db.session.add(tro)
    db.session.flush()
    tr = LabResult(order_id=tro.id, result_value='5.2', result_unit='ng/mL',
                   is_abnormal=True, is_critical=True, status='Verified',
                   result_date=_dt(0, 11, 0))
    lab = _get_user('lab_tech')
    if lab:
        tr.validated_by = lab.id
        tr.created_by = lab.id
    db.session.add(tr)
    ensure_bill_for_lab(tro.id)

    # Creatinine (Kidney Function Test) - abnormal but not critical
    kidney = _lab_test('Kidney Function Test')
    kilo = LabOrder(patient_id=patient.id, doctor_id=doc.id,
                    test_id=kidney.id if kidney else None, status='VERIFIED',
                    priority='Normal', order_date=_dt(0, 10, 20),
                    specimen_type='Blood')
    db.session.add(kilo)
    db.session.flush()
    kr = LabResult(order_id=kilo.id, result_value='1.6', result_unit='mg/dL',
                   is_abnormal=True, is_critical=False, status='Verified',
                   result_date=_dt(0, 11, 0))
    if lab:
        kr.validated_by = lab.id
        kr.created_by = lab.id
    db.session.add(kr)
    ensure_bill_for_lab(kilo.id)

    # Radiology: chest X-ray
    cxr = _add_radiology_order(patient, doc, 'X-Ray', _dt(0, 11, 15),
                               status='Pending', priority='Urgent',
                               scheduled_at=_dt(0, 12, 0),
                               notes='Portable chest X-ray')
    ensure_bill_for_radiology(cxr.id)

    # Bills: consultation
    if appt is not None:
        ensure_bill_for_consultation(appt.id, patient.id, doctor_id=doc.id)

    # Critical alert
    _add_alert(patient, 'CRITICAL_LAB', 'CRITICAL',
               'Critical troponin I level 5.2 ng/mL',
               'Cardiac troponin I critically elevated. Immediate cardiology review required.',
               source_type='lab_order', source_id=tro.id)

    # Timeline
    _add_timeline(patient, 'APPOINTMENT', 'Emergency cardiology visit',
                  'Walk-in emergency appointment with Dr. Khaled', when,
                  source_type='appointment', department='Cardiology')
    _add_timeline(patient, 'LAB', 'Cardiac troponin I: CRITICAL (5.2)',
                  'Critical troponin flagged urgently', _dt(0, 11, 0),
                  source_type='lab_order', source_id=tro.id, department='Pathology')

    # Safety profile for Ahmed (creatinine on file -> CT caution)
    profile = PatientImagingSafetyProfile.query.filter_by(patient_id=patient.id).first()
    if profile is None:
        profile = PatientImagingSafetyProfile(
            patient_id=patient.id, pregnancy_status='Not Applicable',
            last_creatinine=1.6, last_egfr=48.0,
            renal_function_date=_date(0),
            renal_function_notes='Mild renal impairment (Cr 1.6, eGFR 48).')
        db.session.add(profile)

    print('  [P1] Ahmed Mohamed El-Sayed seeded.')


# --------------------------------------------------------------------------- #
# Patient 2 - Fatima (Chronic + penicillin allergy + reconciliation)
# --------------------------------------------------------------------------- #
def _seed_fatima():
    mrn = 'P10002'
    user, patient = _create_patient_user(
        'patient_fatima', 'fatima.hassan@ihis.com', 'Fatima Ali Hassan', mrn,
        date_of_birth=_date(-(65 * 365)), gender='Female', phone='0559876543',
        address='Dubai, UAE', blood_type='A-',
        allergies='Severe penicillin allergy',
        chronic_diseases='Type 2 diabetes, hypothyroidism')
    if MedicalRecord.query.filter_by(patient_id=patient.id).count() > 0:
        print('  [P2] Fatima already seeded, skipping.')
        return

    doc = _create_doctor('dr_sami', 'dr.sami@ihis.com', 'Dr. Sami',
                         'Internal Medicine', fee=200.0)
    pharma = _get_user('pharma') or _create_staff_user(
        'pharma', 'pharma@ihis.com', 'Pharmacist', 'pharmacist', 'Pharmacist',
        department='Pharmacy')

    # Structured allergy (the important one)
    _add_allergy(patient, 'Penicillin', 'Severe anaphylaxis', severity='Severe',
                 verified=True, onset='Adult', recorder_username='admin')
    # Problems
    _add_problem(patient, 'E11.9', 'Type 2 diabetes mellitus (uncontrolled)',
                 _date(-(12 * 365)), severity='Moderate')
    _add_problem(patient, 'E03.9', 'Hypothyroidism', _date(-(8 * 365)),
                 severity='Mild')

    # Past appointment 3 days ago -> referred to clinical pharmacy
    past = _dt(-3, 9, 30)
    _add_appointment(patient, doc, past, status='Completed',
                     reason='Diabetes follow-up, medication review',
                     priority='Normal', visit_type='Scheduled')

    # Vitals (last recorded)
    _add_vital(patient, None,
               blood_pressure_systolic=145, blood_pressure_diastolic=90,
               heart_rate=88, blood_glucose=210, recorded_at=past)

    # Diagnoses
    _add_diagnosis(patient, doc, 'E11.9', 'Type 2 diabetes mellitus without complications', past, True)
    _add_diagnosis(patient, doc, 'E03.9', 'Hypothyroidism, unspecified', past, False)

    # Medical record
    _add_record(patient, doc, 'E11.9 + E03.9',
                'Metformin 1000mg BID, insulin glargine 20u hs, levothyroxine 100mcg AM. Continue and re-check HbA1c.',
                'Poor glycaemic control, random glucose 210. Referred to clinical pharmacy for medication reconciliation.',
                past, status='Signed')

    # Home medications recorded in reconciliation (JSON)
    import json
    home_meds = json.dumps([
        {'name': 'Metformin', 'dose': '1000 mg', 'freq': 'Twice daily'},
        {'name': 'Insulin Glargine', 'dose': '20 units', 'freq': 'Nightly'},
        {'name': 'Levothyroxine', 'dose': '100 mcg', 'freq': 'Morning'},
    ])
    rec = MedicationReconciliation(patient_id=patient.id, pharmacist_id=pharma.id,
                                   reconciliation_type='Ambulatory',
                                   home_medications=home_meds,
                                   summary='3 home meds captured; active prescriptions match. Watch for antibiotic allergy.',
                                   status='Reviewed')
    db.session.add(rec)
    db.session.flush()

    # Reconciliation - active prescriptions (Metformin + Levothyroxine)
    _add_prescription(patient, doc, [
        {'generic': 'Metformin', 'dosage': '1000 mg', 'frequency': 'Twice daily',
         'duration': '90 days', 'instructions': 'After meals', 'quantity': 60},
        {'generic': 'Levothyroxine', 'dosage': '100 mcg', 'frequency': 'Once daily',
         'duration': '90 days', 'instructions': 'Empty stomach AM', 'quantity': 90},
    ], past)

    # Reconciliation discrepancy: suggested amoxicillin would be an ALLERGY conflict
    discrepancy = ReconciliationDiscrepancy(
        reconciliation_id=rec.id,
        medication_id=_med('Amoxicillin').id if _med('Amoxicillin') else None,
        discrepancy_type='ALLERGY',
        description='Amoxicillin would conflict with documented severe penicillin allergy.',
        severity='Severe', recommended_action='Use azithromycin instead.', status='Open')
    db.session.add(discrepancy)

    # Pharmacist intervention re: antibiotic substitution
    amox = _med('Amoxicillin')
    rx_tmp = _add_prescription(patient, doc, [
        {'generic': 'Amoxicillin', 'dosage': '500 mg', 'frequency': 'Three times daily',
         'duration': '7 days', 'instructions': 'Antibiotic - CHECK ALLERGY', 'quantity': 21},
    ], past, status='Active')
    if amox:
        _add_alert(patient, 'ALLERGY', 'SEVERE', 'Penicillin allergy conflict',
                   'Prescribed amoxicillin conflicts with documented severe penicillin allergy. Substitution required.',
                   source_type='prescription', source_id=rx_tmp.id)
        db.session.add(PharmacyIntervention(
            patient_id=patient.id, prescription_id=rx_tmp.id, pharmacist_id=pharma.id,
            prescriber_id=doc.user_id, issue='Amoxicillin is contraindicated in severe penicillin allergy.',
            severity='Contraindicated',
            recommendation='Substitute azithromycin 500mg once daily for 3 days (or clarithromycin).',
            category='ALLERGY', status='ACCEPTED',
            response='Agreed, switching to azithromycin.',))

    # HbA1c result ready
    hba1c = _lab_test('HbA1c')
    hb = LabOrder(patient_id=patient.id, doctor_id=doc.id,
                  test_id=hba1c.id if hba1c else None, status='Finalized',
                  priority='Normal', order_date=past, specimen_type='Blood')
    db.session.add(hb)
    db.session.flush()
    lab = _get_user('lab_tech')
    hr = LabResult(order_id=hb.id, result_value='8.9', result_unit='%', is_abnormal=True,
                   is_critical=False, status='Verified', result_date=_dt(-1, 8, 0))
    if lab:
        hr.validated_by = lab.id
        hr.created_by = lab.id
    db.session.add(hr)
    ensure_bill_for_lab(hb.id)

    # Timeline
    _add_timeline(patient, 'DIAGNOSIS', 'Diabetes + hypothyroidism reviewed',
                  'Follow-up with Dr. Sami', past, department='Internal Medicine')

    print('  [P2] Fatima Ali Hassan seeded.')


# --------------------------------------------------------------------------- #
# Patient 3 - Khaled (Ortho surgery + physio + YOLOv8 fracture)
# --------------------------------------------------------------------------- #
def _seed_khaled():
    mrn = 'P10003'
    user, patient = _create_patient_user(
        'patient_khaled', 'khaled.naimi@ihis.com', 'Khaled Abdullah Al-Naimi', mrn,
        date_of_birth=_date(-(34 * 365)), gender='Male', phone='0561122334',
        address='Abu Dhabi, UAE', blood_type='B+')
    if MedicalRecord.query.filter_by(patient_id=patient.id).count() > 0:
        print('  [P3] Khaled already seeded, skipping.')
        return

    doc = _create_doctor('dr_majed', 'dr.majed@ihis.com', 'Dr. Majed',
                         'Orthopedics', fee=300.0)
    therapist = _create_therapist('physio', 'physio@ihis.com',
                                  'Physical Therapist Peter',
                                  'Orthopedic Rehabilitation')
    nurse = _get_user('nurse')

    # Future orthopedic appointment (tomorrow)
    _add_appointment(patient, doc, _dt(1, 11, 0), status='Scheduled',
                     reason='Post-op follow-up for femur fracture',
                     priority='Normal', visit_type='Scheduled')

    # Admission: orthopedic ward bed 12, admitted 2 days ago, discharge tomorrow
    ward = Ward.query.filter_by(name='General Ward A').first()
    if ward is not None:
        bed12 = (Bed.query.filter_by(ward_id=ward.id, bed_no='B12').first() or
                 Bed.query.filter_by(ward_id=ward.id).first())
        beds_in_ward = Bed.query.filter_by(ward_id=ward.id).order_by(Bed.id).all()
        bed_for_khaled = beds_in_ward[11] if len(beds_in_ward) > 11 else None
        if Admission.query.filter_by(patient_id=patient.id).first() is None:
            adm = Admission(admission_no=f'ADM-{patient.id}',
                            patient_id=patient.id, ward_id=ward.id,
                            bed_id=bed_for_khaled.id if bed_for_khaled else None,
                            admitting_doctor_id=doc.id, admitted_by=nurse.id if nurse else None,
                            admitted_at=_dt(-2, 9, 0),
                            expected_discharge=_dt(1, 12, 0),
                            reason='Right femoral shaft fracture - ORIF done',
                            status='Admitted')
            db.session.add(adm)
            if bed_for_khaled:
                bed_for_khaled.status = 'Occupied'

    # Diagnosis
    when = _dt(-2, 9, 0)
    _add_diagnosis(patient, doc, 'S72.3', 'Fracture of shaft of femur', when, True)
    _add_record(patient, doc, 'S72.3 - Femoral shaft fracture',
                'Open reduction internal fixation (ORIF) performed. 12 physio sessions planned.',
                'Fall from height at work, right leg. Femur fracture confirmed on X-ray.',
                when, status='Signed')

    # Radiology X-ray with report (fracture detected - YOLO candidate)
    xr = _add_radiology_order(patient, doc, 'X-Ray', _dt(-2, 10, 0),
                              status='Performed', priority='Urgent',
                              scheduled_at=_dt(-2, 10, 0),
                              notes='Right femur AP + Lateral')
    rep = _add_radiology_report(xr,
                                'Right femoral shaft shows a transverse fracture with mild displacement.',
                                'Displaced transverse fracture of the right femur.',
                                'ORIF recommended.', status='Draft', when=_dt(-2, 12, 0))
    ensure_bill_for_radiology(xr.id)

    # Radiation dose record for the femur X-ray (measured values from the unit)
    rad = _get_user('radio')
    if ImagingDoseRecord.query.filter_by(order_id=xr.id).count() == 0:
        db.session.add(ImagingDoseRecord(
            patient_id=patient.id, order_id=xr.id, study_date=_dt(-2, 10, 5),
            accession_number=f'ACC-{xr.id}', modality='X-ray',
            body_region='Right femur', study_description='Right femur AP + Lateral radiograph',
            is_estimated=False, dose_metric_type='Air Kerma', dose_value=1.32,
            dose_unit='mGy', air_kerma=1.32, equipment='GE Discovery XR',
            performing_facility='iHIS Radiology', radiologist_id=rad.id if rad else None,
            technologist_id=rad.id if rad else None,
            ordering_physician_id=doc.user_id))

    # CT study with full dose metrics (exercises the dose dashboard + reference levels)
    ct = _add_radiology_order(patient, doc, 'CT Scan', _dt(-2, 11, 0),
                              status='Performed', priority='Normal',
                              scheduled_at=_dt(-2, 11, 0),
                              notes='CT right femur post-ORIF for alignment verification')
    if ImagingDoseRecord.query.filter_by(order_id=ct.id).count() == 0:
        db.session.add(ImagingDoseRecord(
            patient_id=patient.id, order_id=ct.id, study_date=_dt(-2, 11, 30),
            accession_number=f'ACC-CT-{ct.id}', modality='CT',
            body_region='Right femur', study_description='CT right thigh post-op',
            is_estimated=False, dose_metric_type='DLP', dose_value=612.0,
            dose_unit='mGy·cm', ctdi_vol=15.2, dlp=612.0, equipment='Siemens SOMATOM',
            performing_facility='iHIS Radiology', radiologist_id=rad.id if rad else None,
            technologist_id=rad.id if rad else None, ordering_physician_id=doc.user_id))
    ensure_bill_for_radiology(ct.id)

    # Imaging safety profile (exercises patient "My Radiology" portal view)
    if PatientImagingSafetyProfile.query.filter_by(patient_id=patient.id).count() == 0:
        db.session.add(PatientImagingSafetyProfile(
            patient_id=patient.id, pregnancy_status='Not Applicable',
            mri_screening_status='Cleared', mri_screened_at=_dt(-2, 8, 0),
            mri_screened_by=rad.id if rad else None,
            previous_contrast_reaction=False,
            last_creatinine=0.9, last_egfr=95.0, renal_function_date=_date(-2),
            special_preparation_notes='None - healthy adult male.'))

    # Physio plan + assessment + sessions
    assess = TherapyAssessment(patient_id=patient.id, therapist_id=therapist.id,
                               assessment_type='Initial',
                               functional_assessment='Independent but restricted',
                               pain_assessment=6, muscle_strength='3/5 right leg',
                               balance_assessment='Reduced single-leg balance',
                               range_of_motion='Right hip flexion limited',
                               gait_analysis='Antalgic gait with crutches',
                               assessed_at=_dt(-1, 10, 0))
    db.session.add(assess)
    db.session.flush()

    plan = TherapyPlan(patient_id=patient.id, therapist_id=therapist.id,
                       title='Post-ORIF right femur rehabilitation',
                       goals='Regain full hip/knee ROM and ambulation',
                       objectives='12 sessions of strengthening + weight-bearing',
                       interventions='Strengthening, stretching, gait re-education',
                       precautions='No full weight-bearing for 6 weeks',
                       start_date=_date(-1), end_date=_date(60), status='Active')
    db.session.add(plan)
    db.session.flush()

    sess = TherapySession(patient_id=patient.id, therapist_id=therapist.id,
                          plan_id=plan.id, session_type='Individual',
                          scheduled_at=_dt(-1, 11, 0), started_at=_dt(-1, 11, 5),
                          settled_at=_dt(-1, 11, 50), duration_minutes=45,
                          status='Completed', pain_before=6, pain_after=4,
                          exercises_performed='Ankle pumps, isometric quads',
                          modalities='Heat, TENS', patient_response='Good progress',
                          adherence=90, followup_required=True)
    db.session.add(sess)
    db.session.flush()
    ensure_bill_for_physio(sess.id)

    prog = RehabilitationProgress(patient_id=patient.id, plan_id=plan.id,
                                  session_id=sess.id, pain_score=4,
                                  mobility_score=2, strength_score=3,
                                  functional_outcome=2, range_of_motion='Hip flex 80 deg',
                                  balance_score=2, compliance=90, notes='Good')
    db.session.add(prog)

    fout = FunctionalOutcome(patient_id=patient.id, assessment_type='FIM',
                             initial_score=48, current_score=54, target_score=82,
                             recorded_at=_dt(-1, 12, 0))
    db.session.add(fout)

    # Referral from ortho to physiotherapy (care coordination)
    _add_careteam(patient, 'Khaled Ortho Care', [
        ('dr_majed', 'Attending Orthopedic Surgeon'),
        ('physio', 'Rehabilitation Therapist'),
        ('nurse', 'Ward Nurse'),
    ])

    print('  [P3] Khaled Abdullah Al-Naimi seeded.')


# --------------------------------------------------------------------------- #
# Patient 4 - Noora (Dentistry + U-Net + preventive sweep)
# --------------------------------------------------------------------------- #
def _seed_noora():
    mrn = 'P10004'
    user, patient = _create_patient_user(
        'patient_noora', 'noora.kaabi@ihis.com', 'Noura Saeed Al-Kaabi', mrn,
        date_of_birth=_date(-(28 * 365)), gender='Female', phone='0509988776',
        address='Sharjah, UAE', blood_type='AB+',
        allergies='Tetracycline', chronic_diseases='')
    if DentalChart.query.filter_by(patient_id=patient.id).count() > 0:
        print('  [P4] Noora already seeded, skipping.')
        return

    dentist = _create_dentist('dr_youssef', 'dr.youssef@ihis.com', 'Dr. Youssef',
                              'Cosmetic Dentistry')
    # Dr. Youssef also holds a Doctor profile so dental appointments (which
    # reference doctors.id) schedule validly and need-to-know access resolves.
    youssef_doctor = _create_doctor('dr_youssef', 'dr.youssef@ihis.com',
                                    'Dr. Youssef', 'Dentistry', fee=250.0)
    # reuse existing dentist user too for breadth
    _create_dentist('dentist', 'dentist@ihis.com', 'Dr. Dental Dina',
                    'General Dentistry')

    _add_allergy(patient, 'Tetracycline', 'Photosensitivity / discoloration',
                 severity='Moderate', verified=True, onset='Childhood')

    # Future appointment (in a week) for cosmetic filling; preventive cleaning in 3 months
    _add_appointment(patient, youssef_doctor, _dt(7, 14, 0),
                     status='Scheduled', reason='Cosmetic composite filling',
                     visit_type='Scheduled')
    _add_appointment(patient, youssef_doctor,
                     _dt(90, 10, 0), status='Scheduled',
                     reason='Routine dental cleaning', visit_type='Scheduled')

    # Dental chart: tooth 26 deep caries, tooth 11 fracture
    db.session.add(DentalChart(patient_id=patient.id, tooth_number='26',
                               numbering_system='FDI', status='Caries'))
    db.session.add(DentalChart(patient_id=patient.id, tooth_number='11',
                               numbering_system='FDI', status='Crown'))

    # Dental treatment plan + procedure
    plt = DentalTreatmentPlan(patient_id=patient.id, dentist_id=dentist.id,
                              title='Cosmetic restoration',
                              diagnosis='Deep caries 26; fractured 11',
                              status='Planned', start_date=_date(0))
    db.session.add(plt)
    db.session.flush()
    db.session.add(DentalProcedure(patient_id=patient.id, dentist_id=dentist.id,
                                   treatment_plan_id=plt.id,
                                   procedure_name='Composite filling (tooth 26)',
                                   tooth_number='26', status='Planned', cost=350.0,
                                   scheduled_at=_dt(7, 14, 0)))
    db.session.add(DentalProcedure(patient_id=patient.id, dentist_id=dentist.id,
                                   treatment_plan_id=plt.id,
                                   procedure_name='Porcelain veneer (tooth 11)',
                                   tooth_number='11', status='Planned', cost=900.0,
                                   scheduled_at=_dt(14, 14, 0)))

    # Dental image (panoramic OPG for U-Net segmentation)
    db.session.add(DentalImage(patient_id=patient.id, image_type='Panoramic',
                               url='/uploads/demo/opg_noora.png',
                               notes='OPG for tooth segmentation (U-Net demo)'))

    # Immunization: tetanus last dose 8 years ago -> preventive sweep due
    years_ago = _date(-(8 * 365))
    db.session.add(ImmunizationRecord(patient_id=patient.id,
                                      vaccine_name='Tetanus Toxoid', dose_number=5,
                                      administered_at=datetime.combine(years_ago, datetime.min.time()),
                                      site='Left arm',
                                      administered_by=_get_user('nurse').id if _get_user('nurse') else None,
                                      next_due=_date(730), notes='Booster due within 2 years'))

    # Preventive alert triggered by sweep
    _add_alert(patient, 'OVERDUE_FOLLOWUP', 'MODERATE',
               'Tetanus booster due',
               'Last tetanus toxoid given ~8 years ago. Booster recommended within 2 years.',
               source_type='immunization')

    _add_timeline(patient, 'DENTISTRY', 'Dental chart + treatment plan created',
                  'Noura booked cosmetic restoration', _dt(0, 12, 0),
                  department='Dentistry')

    print('  [P4] Noura Saeed Al-Kaabi seeded.')


# --------------------------------------------------------------------------- #
# Patient 5 - Sara (Pediatrics + MDT + patient portal)
# --------------------------------------------------------------------------- #
def _seed_sara():
    mrn = 'P10005'
    user, patient = _create_patient_user(
        'patient_sara', 'sara.mansoori@ihis.com', 'Sara Ibrahim Al-Mansoori', mrn,
        date_of_birth=_date(-(4 * 365)), gender='Female', phone='0544332211',
        address='Dubai, UAE', blood_type='O-',
        chronic_diseases='Mild childhood asthma')
    if MedicalRecord.query.filter_by(patient_id=patient.id).count() > 0:
        print('  [P5] Sara already seeded, skipping.')
        return

    doc = _create_doctor('dr_huda', 'dr.huda@ihis.com', 'Dr. Huda',
                         'Pediatrics', fee=150.0)
    _create_doctor('dr_asma', 'dr.asma@ihis.com', 'Dr. Asma (Allergy)',
                   'Pulmonology', fee=250.0)
    _create_doctor('dr_lina', 'dr.lina@ihis.com', 'Dr. Lina (Nutrition)',
                   'Family Medicine', fee=200.0)

    _add_problem(patient, 'J45.9', 'Mild childhood asthma',
                 _date(-(2 * 365)), severity='Mild')

    # Appointment today 2pm
    when = _dt(0, 14, 0)
    _add_appointment(patient, doc, when, status='CheckedIn',
                     reason='Cough, mild shortness of breath', priority='Normal',
                     visit_type='WalkIn', queue_number=2, checked_in_at=_dt(0, 13, 30))

    # Vitals for a child
    _add_vital(patient, None,
               weight_kg=16.0, height_cm=105.0, oxygen_saturation=96,
               heart_rate=100, respiratory_rate=24, temperature=37.1,
               recorded_at=_dt(0, 13, 35))

    _add_diagnosis(patient, doc, 'J45.9', 'Mild persistent asthma with URI', when, True)
    _add_record(patient, doc, 'J45.9 - Asthma exacerbation',
                'Salbutamol (Ventolin) inhaler weight-based dose; continue controller.',
                'Cough with mild dyspnoea. Chest clear. O2 96%. Maintained on Ventolin.',
                when, status='Signed')

    # Prescription: Salbutamol weight-based
    _add_prescription(patient, doc, [
        {'generic': 'Salbutamol', 'dosage': '2 puffs', 'frequency': 'Every 4-6 hours PRN',
         'duration': '7 days', 'instructions': '2 puffs via spacer PRN', 'quantity': 1},
    ], when)

    # Radiology chest X-ray (result normal/clear)
    cxr = _add_radiology_order(patient, doc, 'X-Ray', _dt(0, 14, 30),
                               status='Performed', priority='Normal',
                               scheduled_at=_dt(0, 15, 0))
    rep = _add_radiology_report(cxr, 'Clear lung fields; no consolidation.',
                                'Normal chest radiograph.', 'No acute abnormality.',
                                status='Signed', when=_dt(0, 16, 0))
    ensure_bill_for_radiology(cxr.id)

    # Care team + MDT case (multi-disciplinary)
    team = _add_careteam(patient, 'Sara Asthma MDT', [
        ('dr_huda', 'Pediatrician'),
        ('dr_asma', 'Allergy Specialist'),
        ('dr_lina', 'Nutritionist'),
    ])
    _add_mdcase(patient, 'Long-term asthma control plan',
                'Pediatrician + allergy + nutrition discussing long-term asthma control. '
                'First meeting held; spacer technique, trigger avoidance, diet reviewed.',
                status='InProgress')

    # Portal message mother->doctor, doctor replied
    _add_message('patient_sara', 'dr_huda', 'Dosage question',
                 'Please confirm the Ventolin dose for my daughter.', _dt(-1, 12, 0))
    _add_message('dr_huda', 'patient_sara', 'Re: Dosage question',
                 '2 puffs via spacer every 4-6 hours as needed. Call if wheeze is not relieved.',
                 _dt(-1, 13, 0), is_read=True)

    # Follow-up
    _add_followup(patient, doc, _dt(14, 10, 0), 'Asthma re-check in 2 weeks',
                  status='Scheduled', creator_username='dr_huda')

    # Timeline
    _add_timeline(patient, 'APPOINTMENT', 'Pediatric asthma visit',
                  'Dr. Huda consultation for suspected asthma exacerbation', when,
                  department='Pediatrics')

    print('  [P5] Sara Ibrahim Al-Mansoori seeded.')


# --------------------------------------------------------------------------- #
# Patient 6 - Alya (Dermatology + skin-lesion AI demo)
# --------------------------------------------------------------------------- #
def _seed_alya():
    mrn = 'P10006'
    user, patient = _create_patient_user(
        'patient_alya', 'alya.mohammed@ihis.com', 'Alya Mohammed Al-Farsi', mrn,
        date_of_birth=_date(-(41 * 365)), gender='Female', phone='0523456789',
        address='Dubai, UAE', blood_type='O+',
        allergies='Sunscreen fragrance')
    derma = _dept('Dermatology')
    if derma and patient.department_id != derma.id:
        patient.department_id = derma.id
        db.session.commit()
    if MedicalRecord.query.filter_by(patient_id=patient.id).count() > 0:
        print('  [P6] Alya already seeded, skipping.')
        return

    doc = _create_doctor('dr_mariam', 'dr.mariam@ihis.com', 'Dr. Mariam',
                         'Dermatology', fee=280.0)

    # Appointment tomorrow for the skin review
    _add_appointment(patient, doc, _dt(1, 10, 30), status='Scheduled',
                     reason='Suspicious pigmented lesion review (left forearm)',
                     priority='Normal', visit_type='Scheduled')

    # Past complaint + diagnosis + record (recent check-up)
    when = _dt(-4, 11, 0)
    _add_problem(patient, 'D48.5', 'Suspected melanocytic naevus',
                 _date(-30), severity='Moderate', recorder_username='admin')
    _add_diagnosis(patient, doc, 'D22.6', 'Melanocytic naevus – left forearm',
                   when, True)
    _add_record(patient, doc, 'D22.6 – melanocytic naevus',
                'Surgical excision of the atypical naevus followed by dermoscopy '
                'review. Patient counselled on sun protection.',
                'New irregularly pigmented 4mm lesion on the left forearm noted. '
                'Dermoscopy borderline; sent for AI dermoscopic triage as a '
                'screening aid before biopsy decision.',
                when, status='Signed')

    # Vitals from the same visit (normal)
    _add_vital(patient, None,
               blood_pressure_systolic=118, blood_pressure_diastolic=76,
               heart_rate=74, temperature=36.8, recorded_at=when)

    # Timeline entry so the AI tooling has a documented context
    _add_timeline(patient, 'DERMATOLOGY', 'Skin lesion reviewed',
                  'Atypical naevus on left forearm – AI triage requested',
                  when, department='Dermatology')

    print('  [P6] Alya Mohammed Al-Farsi seeded.')


# --------------------------------------------------------------------------- #
# Main seed orchestration
# --------------------------------------------------------------------------- #
def _ensure_base_staff():
    """Ensure the shared department accounts exist even on a fresh DB."""
    _create_staff_user('admin', 'admin@ihis.com', 'Hospital Administrator',
                       'admin', 'Admin', department='Administration')
    _create_staff_user('nurse', 'nurse@ihis.com', 'Nurse Nour', 'nurse', 'Nurse',
                       department='Nursing')
    _create_staff_user('lab_tech', 'lab@ihis.com', 'Lab Technician',
                       'lab_technician', 'LabTechnician', department='Pathology')
    _create_staff_user('radio', 'radio@ihis.com', 'Radiology Specialist',
                       'radiologist', 'Radiologist', department='Radiology')
    _create_staff_user('pharma', 'pharma@ihis.com', 'Pharmacist',
                       'pharmacist', 'Pharmacist', department='Pharmacy')
    _create_staff_user('reception', 'reception@ihis.com', 'Receptionist Rana',
                       'receptionist', 'Receptionist', department='Reception')
    _create_staff_user('cashier', 'cashier@ihis.com', 'Cashier Camelia',
                       'cashier', 'Cashier', department='Reception')
    _create_staff_user('superadmin', 'superadmin@ihis.com', 'System Owner',
                       'admin', 'Admin', department='Administration')
    db.session.commit()


def _ensure_medications():
    """Ensure scenario-specific medications + their inventory exist."""
    extra = [
        ('Levothyroxine', 'Synthroid', 'Thyroid hormone'),
        ('Insulin Glargine', 'Lantus', 'Antidiabetic'),
        ('Nitroglycerin', 'Nitrostat', 'Antianginal'),
        ('Azithromycin', 'Zithromax', 'Antibiotic'),
    ]
    for generic, brand, cat in extra:
        if _med(generic) is None:
            db.session.add(Medication(generic_name=generic, brand_name=brand,
                                      category=cat))
            db.session.flush()
    for m in Medication.query.all():
        if PharmacyInventory.query.filter_by(medication_id=m.id).count() == 0:
            db.session.add(PharmacyInventory(medication_id=m.id, quantity=200,
                                             reorder_level=10, unit_cost=5.0,
                                             selling_price=25.0,
                                             expiry_date=_date(365), batch_number=f'BAT-{m.id}'))
    db.session.commit()


def _wipe_patient(patient):
    """Remove all rows referencing a sandbox patient (leaf-first, FK-safe for dev)."""
    pid = patient.id

    def by_col(model, col):
        for row in db.session.query(model).filter(getattr(model, col) == pid).all():
            db.session.delete(row)
        db.session.flush()

    # Results / reports hang off orders, so wipe orders' children first.
    for order in db.session.query(RadiologyOrder).filter_by(patient_id=pid).all():
        rep = order.report
        if rep is not None:
            for ver in list(rep.versions or []):
                db.session.delete(ver)
            db.session.delete(rep)
        for dr in list(order.dose_records or []):
            db.session.delete(dr)
        for cs in list(order.contrast_administrations or []):
            db.session.delete(cs)
        for ss in list(order.safety_screenings or []):
            db.session.delete(ss)
        rec = order.study_record
        if rec is not None:
            db.session.delete(rec)
        db.session.delete(order)
    db.session.flush()
    for order in db.session.query(LabOrder).filter_by(patient_id=pid).all():
        if order.result:
            db.session.delete(order.result)
        db.session.delete(order)
    db.session.flush()

    # Children that link via a parent (not via patient_id)
    for rx in db.session.query(Prescription).filter_by(patient_id=pid).all():
        for item in list(rx.items or []):
            db.session.delete(item)
    for rec in db.session.query(MedicationReconciliation).filter_by(patient_id=pid).all():
        for d in list(rec.discrepancies or []):
            db.session.delete(d)
    db.session.flush()

    for model in [
        Prescription, Diagnosis, MedicalRecord, Appointment,
        VitalSign, NursingNote, CarePlan, DentalProcedure, DentalTreatmentPlan,
        DentalChart, DentalImage, OrthodonticCase, TherapySession, TherapyPlan,
        TherapyAssessment, FunctionalOutcome, RehabilitationProgress,
        TimelineEvent, ClinicalAlert, Allergy, Problem, ImmunizationRecord,
        FollowUp, NursingRiskAssessment, MedicationReconciliation,
        PatientImagingSafetyProfile, PharmacyIntervention, Referral,
        MultidisciplinaryCase, Admission,
    ]:
        by_col(model, 'patient_id')
    # CareTeams -> members, Tasks -> activities (both childless vs patient_id)
    for team in db.session.query(CareTeam).filter_by(patient_id=pid).all():
        for m in list(team.members or []):
            db.session.delete(m)
        db.session.delete(team)
    for task in db.session.query(Task).filter_by(patient_id=pid).all():
        for act in list(task.activities or []):
            db.session.delete(act)
        db.session.delete(task)
    db.session.flush()
    for rec in db.session.query(MedicationReconciliation).filter_by(patient_id=pid).all():
        for d in list(rec.discrepancies or []):
            db.session.delete(d)
    for bill in db.session.query(Bill).filter_by(patient_id=pid).all():
        for bip in list(bill.items):
            db.session.delete(bip)
        for pay in list(bill.payments):
            db.session.delete(pay)
        db.session.delete(bill)
    # Portal messages involving the patient's user
    if patient.user_id:
        for msg in db.session.query(Message).filter(
                (Message.sender_id == patient.user_id) |
                (Message.receiver_id == patient.user_id)).all():
            db.session.delete(msg)
    db.session.flush()


def seed_sandbox_patients(app, rebuild=False):
    db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if db_uri and not db_uri.startswith('sqlite:'):
        print('ERROR: Sandbox patients seed is prohibited for a server '
              '(non-SQLite) database.')
        sys.exit(1)

    with app.app_context():
        print('Seeding sandbox patients (development only)...')
        _ensure_base_staff()
        _ensure_medications()
        if rebuild:
            for mrn in ('P10001', 'P10002', 'P10003', 'P10004', 'P10005',
                        'P10006'):
                pat = _get_patient_by_mrn(mrn)
                if pat:
                    _wipe_patient(pat)
                    db.session.commit()
                    print(f'  [REBUILD] wiped {mrn}.')
        _seed_ahmed()
        db.session.commit()
        _seed_fatima()
        db.session.commit()
        _seed_khaled()
        db.session.commit()
        _seed_noora()
        db.session.commit()
        _seed_sara()
        db.session.commit()
        _seed_alya()
        db.session.commit()
        print('Sandbox patients seeded.')
        print(f'All new accounts use password: {PASSWORD}')


def main():
    rebuild = '--rebuild' in sys.argv
    app = create_app('development')
    seed_roles_and_permissions(app)
    seed_sandbox_patients(app, rebuild=rebuild)


if __name__ == '__main__':
    main()
