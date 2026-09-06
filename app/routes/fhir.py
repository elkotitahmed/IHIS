"""Read-only FHIR R4 feed (gap #18): Patient, $everything bundle + browser view.

All endpoints are access-gated (TIMELINE_VIEW + patient need-to-know), mirror
the same source data the rest of the system shows, and never write state.
"""
import json
from flask import Blueprint, render_template, request, current_app
from flask_login import login_required, current_user
from app import db
from app.models import Patient
from app.access import patient_access_required
from app.routes.decorators import permissions_required
from app.permissions import TIMELINE_VIEW
from app.services.fhir import patient_resources, patient_everything, fhir_patient

fhir_bp = Blueprint('fhir', __name__)


def _fhir_response(payload, status=200):
    return current_app.response_class(
        json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True),
        status=status, mimetype='application/fhir+json')


@fhir_bp.route('/patients/<int:patient_id>')
@login_required
@permissions_required(TIMELINE_VIEW)
@patient_access_required
def patient_resource(patient_id):
    patient = db.session.get(Patient, patient_id)
    return _fhir_response(fhir_patient(patient))


@fhir_bp.route('/patients/<int:patient_id>/$everything')
@login_required
@permissions_required(TIMELINE_VIEW)
@patient_access_required
def patient_everything_route(patient_id):
    patient = db.session.get(Patient, patient_id)
    return _fhir_response(patient_everything(patient))


@fhir_bp.route('/patients/<int:patient_id>/view')
@login_required
@permissions_required(TIMELINE_VIEW)
@patient_access_required
def patient_view(patient_id):
    patient = db.session.get(Patient, patient_id)
    resources = patient_resources(patient)
    summary = []
    for r in resources:
        summary.append({
            'resourceType': r['resourceType'],
            'id': r.get('id'),
            'label': _human_label(r),
        })
    return render_template('fhir/view.html', title='FHIR - ' + patient.user.full_name,
                           patient=patient, resources=summary,
                           bundle_url=request.full_path.replace('/view', '/$everything'))


def _human_label(resource):
    rt = resource.get('resourceType')
    if rt == 'Patient':
        name = resource.get('name')
        if name:
            g = name[0].get('given') or []
            return ' '.join(g) + ' ' + name[0].get('family', '')
        return ''
    for key in ('code', 'vaccineCode', 'medicationCodeableConcept'):
        cc = resource.get(key)
        if cc and cc.get('text'):
            return cc['text']
    for key in ('type',):
        arr = resource.get(key)
        if arr:
            return arr[0].get('text', '')
    return resource.get('id', '')


@fhir_bp.route('/patients')
@login_required
@permissions_required(TIMELINE_VIEW)
def patient_index():
    from app.access import accessible_patient_ids
    pids = accessible_patient_ids(current_user)
    from sqlalchemy.orm import joinedload
    patients = (Patient.query
                .filter(Patient.id.in_(pids or [-1]))
                .options(joinedload(Patient.user))
                .order_by(Patient.id.desc()).limit(200).all())
    entries = [fhir_patient(p) for p in patients]
    payload = {
        'resourceType': 'Bundle',
        'type': 'searchset',
        'total': len(entries),
        'entry': [{'resource': r} for r in entries],
    }
    return _fhir_response(payload)