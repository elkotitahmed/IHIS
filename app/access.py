"""Record-level ("need-to-know") patient access control.

A clinician may open a patient's clinical record only when they have a
documented, legitimate need:

- A patient may access their own record.
- Admin/SuperAdmin retain supervisory access (audit, incident response).
- Otherwise the staff member must have an explicit relationship with the
  patient: a care-team membership, an authored clinical document
  (chart/record/prescription/order/appointment/referral/admission/vital/plan),
  or a documented *operational* relationship that establishes the first
  contact - the patient is registered under the staff member's department,
  is currently admitted (nursing), has an appointment today (nursing), or has
  been referred to the staff member's specialty (physiotherapy / dentistry).

The two public entry points must stay in sync: :func:`has_need_to_know`
answers "may this user open this patient" and :func:`accessible_patient_ids`
answers "which patients may this user list", so a filtered list never yields
a row that then 403s when opened.
"""

from datetime import timedelta
from functools import wraps

from flask import abort
from flask_login import current_user

from app import db
from app.models import (
    Admission,
    Appointment,
    CarePlan,
    CareTeam,
    CareTeamMember,
    DentalProcedure,
    DentalTreatmentPlan,
    Diagnosis,
    LabOrder,
    LabResult,
    MedicalRecord,
    NursingNote,
    OrthodonticCase,
    Patient,
    Prescription,
    RadiologyOrder,
    RadiologyReport,
    Referral,
    TherapyAssessment,
    TherapyPlan,
    TherapySession,
    VitalSign,
)
from app.utils import utcnow

# Specialty keywords that route a referral to a discipline-specific role.
PHYSIO_KEYWORDS = ('physio', 'rehab', 'physical therapy', 'علاج طبيعي')
DENTAL_KEYWORDS = ('dent', 'oral', 'ortho', 'أسنان')

# Referral states that no longer establish a care relationship.
CLOSED_REFERRAL_STATES = ('Rejected', 'REJECTED', 'Closed', 'CLOSED', 'Cancelled')


def _doctor_id(user):
    doc = getattr(user, 'doctor_profile', None)
    return doc.id if doc else None


def _dentist_id(user):
    dent = getattr(user, 'dentist_profile', None)
    return dent.id if dent else None


def _therapist_id(user):
    th = getattr(user, 'therapist_profile', None)
    return th.id if th else None


def _today_window():
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _referral_specialty_filter(keywords):
    clauses = [Referral.to_specialty.ilike(f'%{k}%') for k in keywords]
    return db.or_(*clauses)


def _is_supervisor(user):
    return user.user_type == 'admin' and user.has_any_role('Admin', 'SuperAdmin')


def has_need_to_know(patient, user=None):
    """Return True when ``user`` (default: current_user) may access ``patient``
    under a documented need-to-know policy."""
    user = user or current_user
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.user_type == 'patient':
        return bool(patient) and patient.user_id == user.id
    if _is_supervisor(user):
        return True
    if not patient:
        return False
    pid = patient.id
    uid = user.id

    # Department scoping: a patient registered under the staff member's own
    # department (e.g. Dermatology attachments routed to the dermatologist).
    if user.department_id and patient.department_id == user.department_id:
        return True

    # Care team membership is an explicit assignment valid for any staff role.
    if db.session.query(CareTeamMember.id).join(CareTeam, CareTeamMember.team_id == CareTeam.id).filter(
        CareTeam.patient_id == pid,
        CareTeamMember.user_id == uid,
    ).first():
        return True

    # Doctor: authored clinical documents or appointments.
    did = _doctor_id(user)
    if did and (
        db.session.query(Appointment.id).filter_by(patient_id=pid, doctor_id=did).first()
        or db.session.query(MedicalRecord.id).filter_by(patient_id=pid, doctor_id=did).first()
        or db.session.query(Diagnosis.id).filter_by(patient_id=pid, doctor_id=did).first()
        or db.session.query(Prescription.id).filter_by(patient_id=pid, doctor_id=did).first()
        or db.session.query(LabOrder.id).filter_by(patient_id=pid, doctor_id=did).first()
        or db.session.query(RadiologyOrder.id).filter_by(patient_id=pid, doctor_id=did).first()
        or db.session.query(Admission.id).filter_by(patient_id=pid, admitting_doctor_id=did).first()
        or db.session.query(Referral.id).filter(
            (Referral.patient_id == pid)
            & ((Referral.from_doctor_id == did) | (Referral.to_doctor_id == did))
        ).first()
    ):
        return True

    # Nurse: documented nursing encounters, current inpatients, or patients
    # attending the clinic today.
    if user.has_role('Nurse'):
        if any(
            db.session.query(Model.id).filter_by(patient_id=pid, nurse_id=uid).first()
            for Model in (VitalSign, NursingNote, CarePlan)
        ):
            return True
        if db.session.query(Admission.id).filter_by(patient_id=pid, status='Admitted').first():
            return True
        start, end = _today_window()
        if db.session.query(Appointment.id).filter(
                Appointment.patient_id == pid,
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at < end,
                Appointment.status.notin_(('Cancelled',))).first():
            return True

    # Dentist: documented dental encounters or a referral to dentistry.
    denid = _dentist_id(user)
    if denid and any(
        db.session.query(Model.id).filter_by(patient_id=pid, dentist_id=denid).first()
        for Model in (DentalProcedure, OrthodonticCase, DentalTreatmentPlan)
    ):
        return True
    if user.has_role('Dentist') and db.session.query(Referral.id).filter(
            Referral.patient_id == pid,
            Referral.status.notin_(CLOSED_REFERRAL_STATES),
            _referral_specialty_filter(DENTAL_KEYWORDS)).first():
        return True

    # Physiotherapist: documented therapy encounters or a referral to physio.
    thid = _therapist_id(user)
    if thid and any(
        db.session.query(Model.id).filter_by(patient_id=pid, therapist_id=thid).first()
        for Model in (TherapyAssessment, TherapyPlan, TherapySession)
    ):
        return True
    if user.has_role('Physiotherapist') and db.session.query(Referral.id).filter(
            Referral.patient_id == pid,
            Referral.status.notin_(CLOSED_REFERRAL_STATES),
            _referral_specialty_filter(PHYSIO_KEYWORDS)).first():
        return True

    # Radiologist / radiology technician: authored/signed/performed a study,
    # or the patient has an imaging order in the worklist.
    if (db.session.query(RadiologyReport.id).join(
            RadiologyOrder, RadiologyReport.order_id == RadiologyOrder.id).filter(
            RadiologyOrder.patient_id == pid,
            db.or_(RadiologyReport.reported_by == uid,
                   RadiologyReport.signed_by == uid)).first()):
        return True
    if user.has_any_role('Radiologist', 'RadiologyTechnician') and \
            db.session.query(RadiologyOrder.id).filter_by(patient_id=pid).first():
        return True

    # Lab technician: created or validated a result, or the patient has a lab
    # order in the work queue.
    if (db.session.query(LabResult.id).join(
            LabOrder, LabResult.order_id == LabOrder.id).filter(
            LabOrder.patient_id == pid,
            db.or_(LabResult.created_by == uid,
                   LabResult.validated_by == uid)).first()):
        return True
    if user.has_role('LabTechnician') and db.session.query(LabOrder.id).filter_by(
            patient_id=pid).first():
        return True

    # Pharmacist: the pharmacy worklist is the prescription; a pharmacist
    # legitimately works across all prescriptions for the patient.
    if user.has_role('Pharmacist') and db.session.query(Prescription.id).filter_by(
            patient_id=pid).first():
        return True

    # Receptionist: an operational relationship - booked/checked-in the
    # patient, admitted them, or the patient is attending today.
    if user.has_role('Receptionist'):
        if db.session.query(Appointment.id).filter_by(patient_id=pid, created_by=uid).first():
            return True
        if db.session.query(Admission.id).filter_by(patient_id=pid, admitted_by=uid).first():
            return True
        start, end = _today_window()
        if db.session.query(Appointment.id).filter(
                Appointment.patient_id == pid,
                Appointment.scheduled_at >= start,
                Appointment.scheduled_at < end).first():
            return True

    # Cashier: the patient has a bill the cashier may need to explain.
    if user.has_role('Cashier'):
        from app.models import Bill
        if db.session.query(Bill.id).filter_by(patient_id=pid).first():
            return True

    return False


def accessible_patient_ids(user=None):
    """Return the set of patient ids the current staff member has a documented
    need-to-know relationship with. Mirrors :func:`has_need_to_know` so a
    filtered patient list always yields pages the user may open."""
    user = user or current_user
    if not user.is_authenticated:
        return set()
    if _is_supervisor(user):
        return {pid for (pid,) in db.session.query(Patient.id).all()}
    if user.user_type == 'patient':
        pat = getattr(user, 'patient_profile', None)
        return {pat.id} if pat else set()

    pid_rows = set()
    uid = user.id

    def _ids(query):
        pid_rows.update(pid for (pid,) in query.all() if pid is not None)

    if user.department_id:
        _ids(db.session.query(Patient.id).filter_by(department_id=user.department_id))

    _ids(db.session.query(CareTeam.patient_id).join(
        CareTeamMember, CareTeamMember.team_id == CareTeam.id).filter(
        CareTeamMember.user_id == uid))

    did = _doctor_id(user)
    if did:
        _ids(db.session.query(Appointment.patient_id).filter_by(doctor_id=did))
        _ids(db.session.query(MedicalRecord.patient_id).filter_by(doctor_id=did))
        _ids(db.session.query(Diagnosis.patient_id).filter_by(doctor_id=did))
        _ids(db.session.query(Prescription.patient_id).filter_by(doctor_id=did))
        _ids(db.session.query(LabOrder.patient_id).filter_by(doctor_id=did))
        _ids(db.session.query(RadiologyOrder.patient_id).filter_by(doctor_id=did))
        _ids(db.session.query(Admission.patient_id).filter_by(admitting_doctor_id=did))
        _ids(db.session.query(Referral.patient_id).filter(
            (Referral.from_doctor_id == did) | (Referral.to_doctor_id == did)))

    if user.has_role('Nurse'):
        for Model in (VitalSign, NursingNote, CarePlan):
            _ids(db.session.query(Model.patient_id).filter_by(nurse_id=uid))
        _ids(db.session.query(Admission.patient_id).filter_by(status='Admitted'))
        start, end = _today_window()
        _ids(db.session.query(Appointment.patient_id).filter(
            Appointment.scheduled_at >= start, Appointment.scheduled_at < end,
            Appointment.status.notin_(('Cancelled',))))

    denid = _dentist_id(user)
    if denid:
        for Model in (DentalProcedure, OrthodonticCase, DentalTreatmentPlan):
            _ids(db.session.query(Model.patient_id).filter_by(dentist_id=denid))
    if user.has_role('Dentist'):
        _ids(db.session.query(Referral.patient_id).filter(
            Referral.status.notin_(CLOSED_REFERRAL_STATES),
            _referral_specialty_filter(DENTAL_KEYWORDS)))

    thid = _therapist_id(user)
    if thid:
        for Model in (TherapyAssessment, TherapyPlan, TherapySession):
            _ids(db.session.query(Model.patient_id).filter_by(therapist_id=thid))
    if user.has_role('Physiotherapist'):
        _ids(db.session.query(Referral.patient_id).filter(
            Referral.status.notin_(CLOSED_REFERRAL_STATES),
            _referral_specialty_filter(PHYSIO_KEYWORDS)))

    _ids(db.session.query(RadiologyOrder.patient_id).join(
        RadiologyReport, RadiologyReport.order_id == RadiologyOrder.id).filter(
        db.or_(RadiologyReport.reported_by == uid, RadiologyReport.signed_by == uid)))
    if user.has_any_role('Radiologist', 'RadiologyTechnician'):
        _ids(db.session.query(RadiologyOrder.patient_id).distinct())

    _ids(db.session.query(LabOrder.patient_id).join(
        LabResult, LabResult.order_id == LabOrder.id).filter(
        db.or_(LabResult.created_by == uid, LabResult.validated_by == uid)))
    if user.has_role('LabTechnician'):
        _ids(db.session.query(LabOrder.patient_id).distinct())

    if user.has_role('Pharmacist'):
        _ids(db.session.query(Prescription.patient_id).distinct())

    if user.has_role('Receptionist'):
        _ids(db.session.query(Appointment.patient_id).filter_by(created_by=uid))
        _ids(db.session.query(Admission.patient_id).filter_by(admitted_by=uid))
        start, end = _today_window()
        _ids(db.session.query(Appointment.patient_id).filter(
            Appointment.scheduled_at >= start, Appointment.scheduled_at < end))

    if user.has_role('Cashier'):
        from app.models import Bill
        _ids(db.session.query(Bill.patient_id).distinct())

    return pid_rows


def require_patient_access(patient):
    """Abort with 403 unless the current user has need-to-know access to the
    given patient. ``None`` patient is allowed through so the caller can
    handle a missing-record case itself."""
    if patient is None:
        return
    if not has_need_to_know(patient):
        abort(403)


def patient_access_required(f):
    """Route decorator: resolves ``patient_id`` (path kwarg) to a Patient and
    guards it. 404 on unknown patient, 403 when the current user has no
    documented need-to-know."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        patient_id = kwargs.get('patient_id')
        if patient_id is not None:
            patient = db.session.get(Patient, patient_id)
            if patient is None:
                abort(404)
            require_patient_access(patient)
        return f(*args, **kwargs)
    return wrapper
