"""Record-level ("need-to-know") patient access control.

A clinician may open a patient's clinical record only when they have a
documented, legitimate need:

- A patient may access their own record.
- Admin/SuperAdmin retain supervisory access (audit, incident response).
- Otherwise the staff member must have an explicit relationship with the
  patient: a care-team membership, or an authored clinical document
  (chart/record/prescription/order/appointment/referral/admission/vital/plan).
"""

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


def _doctor_id(user):
    doc = getattr(user, 'doctor_profile', None)
    return doc.id if doc else None


def _dentist_id(user):
    dent = getattr(user, 'dentist_profile', None)
    return dent.id if dent else None


def _therapist_id(user):
    th = getattr(user, 'therapist_profile', None)
    return th.id if th else None


def has_need_to_know(patient, user=None):
    """Return True when ``user`` (default: current_user) may access ``patient``
    under a documented need-to-know policy."""
    user = user or current_user
    if user.user_type == 'patient':
        return bool(patient) and patient.user_id == user.id
    if user.user_type == 'admin' and user.has_any_role('Admin', 'SuperAdmin'):
        return True
    if not patient:
        return False
    pid = patient.id
    uid = user.id

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

    # Nurse: documented nursing encounters.
    if any(
        db.session.query(Model.id).filter_by(patient_id=pid, nurse_id=uid).first()
        for Model in (VitalSign, NursingNote, CarePlan)
    ):
        return True

    # Dentist: documented dental encounters.
    denid = _dentist_id(user)
    if denid and any(
        db.session.query(Model.id).filter_by(patient_id=pid, dentist_id=denid).first()
        for Model in (DentalProcedure, OrthodonticCase)
    ):
        return True

    # Physiotherapist: documented therapy encounters.
    thid = _therapist_id(user)
    if thid and any(
        db.session.query(Model.id).filter_by(patient_id=pid, therapist_id=thid).first()
        for Model in (TherapyAssessment, TherapyPlan, TherapySession)
    ):
        return True

    # Radiologist: authored/signed a report (or the ordering doctor already has
    # access via the doctor branch above) for one of the patient's studies.
    if (db.session.query(RadiologyReport.id).join(
            RadiologyOrder, RadiologyReport.order_id == RadiologyOrder.id).filter(
            RadiologyOrder.patient_id == pid,
            db.or_(RadiologyReport.reported_by == uid,
                   RadiologyReport.signed_by == uid)).first()):
        return True

    # Lab technician: created or validated a result for one of the patient's
    # lab orders.
    if (db.session.query(LabResult.id).join(
            LabOrder, LabResult.order_id == LabOrder.id).filter(
            LabOrder.patient_id == pid,
            db.or_(LabResult.created_by == uid,
                   LabResult.validated_by == uid)).first()):
        return True

    # Pharmacist: the pharmacy worklist is the prescription; a pharmacist
    # legitimately works across all prescriptions for the patient.
    if user.has_role('Pharmacist') and db.session.query(Prescription.id).filter_by(
            patient_id=pid).first():
        return True

    return False


def accessible_patient_ids(user=None):
    """Return the set of patient ids the current staff member has a documented
    need-to-know relationship with. For a Doctor this mirrors the doctor branch
    of :func:`has_need_to_know` so a filtered patient list always yields
    overview/detail pages the user is actually allowed to open."""
    user = user or current_user
    if not user.is_authenticated:
        return set()
    if user.user_type == 'admin' and user.has_any_role('Admin', 'SuperAdmin'):
        return {pid for (pid,) in db.session.query(Patient.id).all()}
    if user.user_type == 'patient':
        pat = getattr(user, 'patient_profile', None)
        return {pat.id} if pat else set()

    pid_rows = set()
    uid = user.id

    if db.session.query(CareTeamMember.id).join(
            CareTeam, CareTeamMember.team_id == CareTeam.id).filter(
            CareTeamMember.user_id == uid).all():
        for (pid,) in db.session.query(CareTeam.patient_id).join(
                CareTeamMember, CareTeamMember.team_id == CareTeam.id).filter(
                CareTeamMember.user_id == uid).all():
            pid_rows.add(pid)

    did = _doctor_id(user)
    if did:
        pid_rows.update(pid for (pid,) in db.session.query(Appointment.patient_id).filter_by(doctor_id=did).all())
        pid_rows.update(pid for (pid,) in db.session.query(MedicalRecord.patient_id).filter_by(doctor_id=did).all())
        pid_rows.update(pid for (pid,) in db.session.query(Diagnosis.patient_id).filter_by(doctor_id=did).all())
        pid_rows.update(pid for (pid,) in db.session.query(Prescription.patient_id).filter_by(doctor_id=did).all())
        pid_rows.update(pid for (pid,) in db.session.query(LabOrder.patient_id).filter_by(doctor_id=did).all())
        pid_rows.update(pid for (pid,) in db.session.query(RadiologyOrder.patient_id).filter_by(doctor_id=did).all())
        pid_rows.update(pid for (pid,) in db.session.query(Admission.patient_id).filter_by(admitting_doctor_id=did).all())
        pid_rows.update(pid for (pid,) in db.session.query(Referral.patient_id).filter(
            (Referral.from_doctor_id == did) | (Referral.to_doctor_id == did)).all())
    else:
        # Non-doctor staff roles still need their documented encounters.
        for Model in (VitalSign, NursingNote, CarePlan):
            pid_rows.update(pid for (pid,) in db.session.query(Model.patient_id).filter_by(nurse_id=uid).all())
        denid = _dentist_id(user)
        if denid:
            for Model in (DentalProcedure, OrthodonticCase):
                pid_rows.update(pid for (pid,) in db.session.query(Model.patient_id).filter_by(dentist_id=denid).all())
        thid = _therapist_id(user)
        if thid:
            for Model in (TherapyAssessment, TherapyPlan, TherapySession):
                pid_rows.update(pid for (pid,) in db.session.query(Model.patient_id).filter_by(therapist_id=thid).all())
        pid_rows.update(pid for (pid,) in db.session.query(RadiologyReport.patient_id).join(
            RadiologyOrder, RadiologyReport.order_id == RadiologyOrder.id).filter(
            db.or_(RadiologyReport.reported_by == uid, RadiologyReport.signed_by == uid)).all())
        pid_rows.update(pid for (pid,) in db.session.query(LabResult.patient_id).join(
            LabOrder, LabResult.order_id == LabOrder.id).filter(
            db.or_(LabResult.created_by == uid, LabResult.validated_by == uid)).all())
        if user.has_role('Pharmacist'):
            pid_rows.update(pid for (pid,) in db.session.query(Prescription.patient_id).distinct().all())

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
            from app.models import Patient
            patient = db.session.get(Patient, patient_id)
            if patient is None:
                abort(404)
            require_patient_access(patient)
        return f(*args, **kwargs)
    return wrapper