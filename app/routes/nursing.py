from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from app import db
from app.models import (
    VitalSign, NursingNote, MedicationAdministration, CarePlan,
    Patient, Prescription, Medication, User, PrescriptionItem, IntakeOutput,
)
from app.routes.decorators import roles_required, permissions_required, log_activity
from app.access import patient_access_required, accessible_patient_ids, require_patient_access
from app.services.timeline import record_event
from app.services import alerts as alert_svc
from app.services.patient_safety import patient_safety_context
from app.utils import utcnow

nursing_bp = Blueprint('nursing', __name__)


def _as_int(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _as_float(value):
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _as_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date() if value else None
    except (TypeError, ValueError):
        return None


def _is_abnormal(vital):
    if vital.temperature is not None and (vital.temperature > 38.0 or vital.temperature < 36.0):
        return True
    if vital.heart_rate is not None and (vital.heart_rate > 100 or vital.heart_rate < 60):
        return True
    if vital.respiratory_rate is not None and vital.respiratory_rate > 20:
        return True
    if vital.oxygen_saturation is not None and vital.oxygen_saturation < 90:
        return True
    if vital.blood_pressure_systolic is not None and \
            (vital.blood_pressure_systolic > 140 or vital.blood_pressure_systolic < 90):
        return True
    if vital.blood_pressure_diastolic is not None and \
            (vital.blood_pressure_diastolic > 90 or vital.blood_pressure_diastolic < 60):
        return True
    return False


@nursing_bp.route('/dashboard')
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def dashboard():
    """Nurse workspace: the patients this nurse is responsible for today
    (inpatients, today's clinic attendances, care-team assignments), doses
    that are due, and abnormal observations."""
    from app.models import Admission, Appointment
    pids = accessible_patient_ids(current_user)
    id_list = sorted(pids) if pids else [-1]
    patients = (Patient.query.filter(Patient.id.in_(id_list))
                .order_by(Patient.id.desc()).limit(100).all())
    admitted = {a.patient_id: a for a in Admission.query.filter(
        Admission.status == 'Admitted', Admission.patient_id.in_(id_list)).all()}
    now = utcnow()
    due_doses = (MedicationAdministration.query
                 .filter(MedicationAdministration.patient_id.in_(id_list),
                         MedicationAdministration.status.in_(['Scheduled', 'Due']))
                 .order_by(MedicationAdministration.scheduled_time.asc().nulls_last())
                 .limit(20).all())
    overdue_doses = [d for d in due_doses if d.scheduled_time and d.scheduled_time < now]
    # Latest vital per patient in a single pass.
    latest_by_pid = {}
    for v in (VitalSign.query.filter(VitalSign.patient_id.in_(id_list))
              .order_by(VitalSign.recorded_at.desc()).all()):
        latest_by_pid.setdefault(v.patient_id, v)
    abnormal = [latest_by_pid[pid] for pid in latest_by_pid if _is_abnormal(latest_by_pid[pid])]
    vitals_due = [p for p in patients if p.id in admitted and (
        p.id not in latest_by_pid or
        (now - latest_by_pid[p.id].recorded_at).total_seconds() > 8 * 3600)]
    my_tasks = []
    from app.models import Task
    my_tasks = (Task.query.filter(
        db.or_(Task.assigned_to == current_user.id,
               db.and_(Task.assigned_role == 'Nurse', Task.assigned_to.is_(None))),
        Task.status.in_(['NEW', 'ASSIGNED', 'IN_PROGRESS']))
        .order_by(Task.due_at.asc().nulls_last(), Task.created_at.desc()).limit(10).all())
    return render_template('nursing/dashboard.html', title='Nursing Dashboard',
                           assigned_patients=len(patients), med_count=len(due_doses),
                           critical_alerts=len(abnormal), patients=patients,
                           admitted=admitted, due_doses=due_doses,
                           overdue_doses=overdue_doses, abnormal=abnormal,
                           vitals_due=vitals_due, latest_by_pid=latest_by_pid,
                           my_tasks=my_tasks, now=now)


@nursing_bp.route('/patients')
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def patients():
    search = request.args.get('q', '')
    pids = accessible_patient_ids(current_user)
    query = Patient.query.filter(Patient.id.in_(sorted(pids) if pids else [-1]))
    if search:
        query = query.join(Patient.user).filter(
            db.or_(User.full_name.ilike(f'%{search}%'),
                   User.email.ilike(f'%{search}%'),
                   Patient.mrn.ilike(f'%{search}%')))
    results = query.order_by(Patient.id.desc()).limit(100).all()
    return render_template('nursing/patients.html', title='Nursing - Patients',
                           patients=results, search=search)


@nursing_bp.route('/patients/<int:patient_id>/vitals', methods=['GET', 'POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
@permissions_required('VITALS_CREATE')
@patient_access_required
def vitals(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        vital = VitalSign(
            patient_id=patient.id,
            nurse_id=current_user.id,
            temperature=_as_float(request.form.get('temperature')),
            blood_pressure_systolic=_as_int(request.form.get('blood_pressure_systolic')),
            blood_pressure_diastolic=_as_int(request.form.get('blood_pressure_diastolic')),
            heart_rate=_as_int(request.form.get('heart_rate')),
            respiratory_rate=_as_int(request.form.get('respiratory_rate')),
            oxygen_saturation=_as_int(request.form.get('oxygen_saturation')),
            height_cm=_as_float(request.form.get('height_cm')),
            weight_kg=_as_float(request.form.get('weight_kg')),
            pain_score=_as_int(request.form.get('pain_score')),
            blood_glucose=_as_float(request.form.get('blood_glucose')),
        )
        db.session.add(vital)
        db.session.flush()
        summary = (f'BP {vital.blood_pressure_systolic}/{vital.blood_pressure_diastolic} · '
                   f'HR {vital.heart_rate} · RR {vital.respiratory_rate} · '
                   f'Temp {vital.temperature} · SpO2 {vital.oxygen_saturation}')
        record_event(patient.id, 'VITALS', 'Vital signs recorded', summary,
                     source_type='vital_sign', source_id=vital.id,
                     department='Nursing')
        if _is_abnormal(vital):
            abnormal = []
            if vital.temperature is not None and (vital.temperature > 38.0 or vital.temperature < 36.0):
                abnormal.append(f'Temp {vital.temperature}')
            if vital.heart_rate is not None and (vital.heart_rate > 100 or vital.heart_rate < 60):
                abnormal.append(f'HR {vital.heart_rate}')
            if vital.respiratory_rate is not None and vital.respiratory_rate > 20:
                abnormal.append(f'RR {vital.respiratory_rate}')
            if vital.oxygen_saturation is not None and vital.oxygen_saturation < 90:
                abnormal.append(f'SpO2 {vital.oxygen_saturation}')
            if vital.blood_pressure_systolic is not None and \
                    (vital.blood_pressure_systolic > 140 or vital.blood_pressure_systolic < 90):
                abnormal.append(f'BP {vital.blood_pressure_systolic}/{vital.blood_pressure_diastolic}')
            alert_svc.ensure_open_alert(
                patient.id, 'ABNORMAL_VITALS', severity='LOW',
                title=f'Abnormal vitals: {", ".join(abnormal)}',
                message=f'Recorded at {utcnow().strftime("%H:%M")} · {summary}',
                source_type='vital_sign', source_id=vital.id)
        # Standard early-warning scores (deterministic, explainable)
        from app.services.clinical_scores import news2, qsofa
        n2 = news2(vital, on_oxygen=request.form.get('on_oxygen') == '1',
                   consciousness=request.form.get('consciousness') or 'A')
        if n2['score'] >= 5 or n2['band'] == 'LOW-MEDIUM':
            alert_svc.ensure_open_alert(
                patient.id, 'NEWS2', severity='CRITICAL' if n2['score'] >= 7 else 'HIGH' if n2['score'] >= 5 else 'MODERATE',
                title=f"NEWS2 {n2['score']} ({n2['band']}){' — partial' if n2['partial'] else ''}",
                message=f"{summary} · {n2['advice']}", source_type='vital_sign', source_id=vital.id)
        qs = qsofa(vital, altered_mentation=(request.form.get('consciousness') or 'A').upper() != 'A')
        if qs['positive']:
            alert_svc.ensure_open_alert(
                patient.id, 'SEPSIS_RISK', severity='HIGH',
                title=f"qSOFA {qs['score']}/3 — sepsis risk", message=f"{summary} · {qs['advice']}",
                source_type='vital_sign', source_id=vital.id)
        log_activity('CREATE_VITAL_SIGN', 'patient', patient.id,
                     f'Nurse {current_user.id} recorded vital signs')
        db.session.commit()
        flash(f"Vital signs recorded. NEWS2 {n2['score']} ({n2['band']}).", 'success')
        return redirect(url_for('nursing.vitals', patient_id=patient.id))
    vitals_list = VitalSign.query.filter_by(patient_id=patient.id).order_by(
        VitalSign.recorded_at.desc()).all()
    return render_template('nursing/vitals.html', title='Vital Signs',
                           patient=patient, vitals=vitals_list)


@nursing_bp.route('/patients/<int:patient_id>/notes', methods=['GET', 'POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
@permissions_required('NURSING_NOTE_CREATE')
@patient_access_required
def notes(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        note = NursingNote(
            patient_id=patient.id,
            nurse_id=current_user.id,
            note=request.form.get('note'),
            shift=request.form.get('shift'),
        )
        if not (note.note or '').strip():
            flash('Nursing note text is required.', 'warning')
            return redirect(url_for('nursing.notes', patient_id=patient.id))
        db.session.add(note)
        db.session.flush()
        record_event(patient.id, 'NURSING', 'Nursing note',
                     (note.note or '')[:160] + (f' · {note.shift} shift' if note.shift else ''),
                     source_type='nursing_note', source_id=note.id, department='Nursing')
        log_activity('CREATE_NURSING_NOTE', 'patient', patient.id,
                     f'Nurse {current_user.id} added a nursing note')
        db.session.commit()
        flash('Nursing note added.', 'success')
        return redirect(url_for('nursing.notes', patient_id=patient.id))
    notes_list = NursingNote.query.filter_by(patient_id=patient.id).order_by(
        NursingNote.created_at.desc()).all()
    return render_template('nursing/notes.html', title='Nursing Notes',
                           patient=patient, notes=notes_list)


@nursing_bp.route('/patients/<int:patient_id>/care-plan', methods=['GET', 'POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
@permissions_required('CARE_PLAN_CREATE')
@patient_access_required
def care_plan(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        plan = CarePlan(
            patient_id=patient.id,
            nurse_id=current_user.id,
            title=request.form.get('title'),
            goals=request.form.get('goals'),
            interventions=request.form.get('interventions'),
            start_date=_as_date(request.form.get('start_date')) or date.today(),
            end_date=_as_date(request.form.get('end_date')),
        )
        db.session.add(plan)
        db.session.flush()
        record_event(patient.id, 'NURSING', f'Care plan: {plan.title or "Nursing care plan"}',
                     (plan.goals or '')[:160], source_type='care_plan', source_id=plan.id,
                     department='Nursing')
        log_activity('CREATE_CARE_PLAN', 'patient', patient.id,
                     f'Nurse {current_user.id} created a care plan')
        db.session.commit()
        flash('Care plan created.', 'success')
        return redirect(url_for('nursing.care_plan', patient_id=patient.id))
    plans = CarePlan.query.filter_by(patient_id=patient.id).order_by(
        CarePlan.start_date.desc()).all()
    return render_template('nursing/care_plans.html', title='Care Plan',
                           patient=patient, plans=plans, today=date.today())


@nursing_bp.route('/medication-schedule')
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
def medication_schedule():
    # Eager-load patient, prescription and its items/medications in one query
    # to avoid the N+1 pattern of per-record lookups.
    pids = accessible_patient_ids(current_user)
    recs = (MedicationAdministration.query
            .filter(MedicationAdministration.patient_id.in_(sorted(pids) if pids else [-1]))
            .options(
                db.joinedload(MedicationAdministration.patient)
                .joinedload(Patient.user),
                db.joinedload(MedicationAdministration.prescription)
                .joinedload(Prescription.items),
            )
            .order_by(MedicationAdministration.status.asc(),
                      MedicationAdministration.scheduled_time.desc().nulls_last())
            .limit(300).all())
    lang = getattr(current_user, 'lang', 'en') or 'en'
    items = []
    nurse_map = {u.id: u for u in
                 User.query.filter(User.id.in_(
                     {r.nurse_id for r in recs if r.nurse_id})).all()}
    for rec in recs:
        line = rec.prescription_item or (
            rec.prescription.items[0] if rec.prescription and rec.prescription.items else None)
        medication = rec.medication or (line.medication if line else None)
        nurse = nurse_map.get(rec.nurse_id)
        items.append({
            'record': rec,
            'patient': rec.patient,
            'medication_name': medication.generic_name if medication else 'N/A',
            'dosage': line.dosage if line else None,
            'frequency': line.frequency if line else None,
            'nurse_name': nurse.full_name if nurse else 'N/A',
        })
    return render_template('nursing/med_schedule.html', title='Medication Schedule',
                           items=items)


def _parse_dt(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


MED_OUTCOMES = ('Administered', 'Refused', 'Held', 'Missed', 'Discontinued')


@nursing_bp.route('/patients/<int:patient_id>/mar', methods=['GET', 'POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
@permissions_required('MEDICATION_ADMIN')
@patient_access_required
def mar(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        item = None
        item_id = _as_int(request.form.get('prescription_item_id'))
        if item_id:
            item = db.session.get(PrescriptionItem, item_id)
        if item is None or item.prescription.patient_id != patient.id:
            flash('Invalid prescription item.', 'danger')
            return redirect(url_for('nursing.mar', patient_id=patient.id))
        if item.prescription.status == 'Cancelled' or item.status == 'Cancelled':
            flash('This prescription item has been cancelled; no doses can be scheduled.', 'danger')
            return redirect(url_for('nursing.mar', patient_id=patient.id))
        due = _parse_dt(request.form.get('scheduled_time'))
        admin = MedicationAdministration(
            patient_id=patient.id,
            prescription_id=item.prescription_id,
            prescription_item_id=item.id,
            medication_id=item.medication_id,
            scheduled_time=due,
            dose_given=request.form.get('dose_given') or item.dosage,
            route=request.form.get('route'),
            status='Scheduled',
        )
        db.session.add(admin)
        log_activity('SCHEDULE_MED_ADMIN', 'patient', patient.id,
                     f'item={item.id} med={item.medication_id}')
        db.session.commit()
        flash('Scheduled dose added.', 'success')
        return redirect(url_for('nursing.mar', patient_id=patient.id))

    active_rx = Prescription.query.filter(
        Prescription.patient_id == patient.id,
        Prescription.status.notin_(['Cancelled', 'Dispensed']),
    ).all()
    pending = MedicationAdministration.query.filter(
        MedicationAdministration.patient_id == patient.id,
        MedicationAdministration.status.in_(['Scheduled', 'Due']),
    ).order_by(MedicationAdministration.scheduled_time.asc().nulls_last(),
               MedicationAdministration.id.asc()).all()
    history = MedicationAdministration.query.filter(
        MedicationAdministration.patient_id == patient.id,
        MedicationAdministration.status.notin_(['Scheduled', 'Due']),
    ).order_by(MedicationAdministration.scheduled_time.desc().nulls_last(),
               MedicationAdministration.id.desc()).all()
    now = utcnow()
    return render_template('nursing/mar.html', title='Medication Administration Record',
                           patient=patient, active_rx=active_rx,
                           pending=pending, history=history, now=now,
                           **patient_safety_context(patient.id),
                           today=utcnow().date())


@nursing_bp.route('/administration/<int:admin_id>/outcome', methods=['POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
@permissions_required('MEDICATION_ADMIN')
def administration_outcome(admin_id):
    admin = db.session.get(MedicationAdministration, admin_id)
    if admin is None:
        abort(404)
    require_patient_access(admin.patient)
    outcome = request.form.get('status')
    if outcome not in MED_OUTCOMES:
        flash('Invalid outcome.', 'danger')
        return redirect(url_for('nursing.mar', patient_id=admin.patient_id))
    if admin.status not in ('Scheduled', 'Due', 'Held'):
        flash(f'This dose is already recorded as {admin.status}.', 'info')
        return redirect(url_for('nursing.mar', patient_id=admin.patient_id))
    if outcome == 'Administered' and admin.prescription and admin.prescription.status == 'Cancelled':
        flash('The prescription was cancelled by the physician; the dose cannot be given.', 'danger')
        return redirect(url_for('nursing.mar', patient_id=admin.patient_id))
    admin.status = outcome
    admin.nurse_id = current_user.id
    admin.administered_at = utcnow()
    admin.reason = request.form.get('reason') if outcome != 'Administered' else None
    admin.notes = request.form.get('notes') or None
    med_label = (admin.medication.generic_name if admin.medication else admin.dose_given) or 'medication'
    record_event(admin.patient_id, 'NURSING', f'Medication {outcome.lower()}: {med_label}',
                 (admin.dose_given or '') + (f' · {admin.reason}' if admin.reason else ''),
                 source_type='medication_administration', source_id=admin.id,
                 department='Nursing')
    log_activity('MED_ADMIN_OUTCOME', 'patient', admin.patient_id,
                 f'admin={admin.id} -> {outcome}')
    from app.services.notifications import notify_doctor, notify_patient
    doctor = admin.prescription.doctor if admin.prescription else None
    if outcome != 'Administered':
        reason = (admin.reason or 'no reason given').strip() or 'no reason given'
        if doctor:
            notify_doctor(doctor,
                          f'Medication {outcome.lower()}',
                          f'Medication "{admin.dose_given or "-"}" was {outcome.lower()} '
                          f'({reason}). Prescription #{admin.prescription_id}.',
                          notification_type='critical' if outcome == 'Missed' else 'in-app',
                          entity_type='prescription', entity_id=admin.prescription_id)
    else:
        notify_patient(admin.patient,
                       'Medication administered',
                       f'A dose of {admin.dose_given or "medication"} was given.',
                       entity_type='prescription', entity_id=admin.prescription_id)
    db.session.commit()
    flash(f'Dose marked {outcome}.', 'success')
    return redirect(url_for('nursing.mar', patient_id=admin.patient_id))


@nursing_bp.route('/patients/<int:patient_id>/intake-output', methods=['GET', 'POST'])
@login_required
@roles_required('Nurse', 'Admin', 'SuperAdmin')
@permissions_required('INTAKE_OUTPUT_RECORD')
@patient_access_required
def intake_output(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    if request.method == 'POST':
        rec = IntakeOutput(
            patient_id=patient.id,
            nurse_id=current_user.id,
            intake_type=request.form.get('intake_type') or None,
            intake_ml=_as_int(request.form.get('intake_ml')),
            output_type=request.form.get('output_type') or None,
            output_ml=_as_int(request.form.get('output_ml')),
            notes=request.form.get('notes') or None,
        )
        db.session.add(rec)
        log_activity('RECORD_INT_OUT', 'patient', patient.id,
                     f'intake={rec.intake_ml} out={rec.output_ml}')
        db.session.commit()
        flash('Intake/Output recorded.', 'success')
        return redirect(url_for('nursing.intake_output', patient_id=patient.id))
    records = IntakeOutput.query.filter_by(patient_id=patient.id).order_by(
        IntakeOutput.recorded_at.desc()).all()
    net = sum((r.intake_ml or 0) - (r.output_ml or 0) for r in records)
    total_in = sum(r.intake_ml or 0 for r in records)
    total_out = sum(r.output_ml or 0 for r in records)
    return render_template('nursing/intake_output.html', title='Intake / Output',
                           patient=patient, records=records, net=net,
                           total_in=total_in, total_out=total_out)