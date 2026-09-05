"""Read-only FHIR R4 serializers for iHIS clinical records (gap #18).

Maps native models to standard FHIR R4 resources so any FHIR-aware consumer
(fhir-ui style viewers, Mirth/HL7 bridges, research exports) can read the
patient record. All resources are built from live, access-gated data in the
request; nothing here writes or mutates state.
"""
import uuid

from app.utils import utcnow


def _iso(value):
    """Datetime/date -> FHIR instant/dateTime; None -> None."""
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        s = value.isoformat()
        return s if 'T' in s else s + 'T00:00:00'
    return str(value)


def _code_ref(system, code, display=None):
    return {'system': system, 'code': code, **({'display': display} if display else {})}


def _codeable(text, system=None, code=None):
    out = {'text': text}
    if system and code:
        out['coding'] = [_code_ref(system, code, text)]
    return out


def _ident(system, value, label=None):
    if not value:
        return None
    return {'system': system, 'value': str(value), **({'type': {'text': label}} if label else {})}


def _fhir_ref(resource_type, fhir_id, display=None):
    ref = {'reference': f'{resource_type}/{fhir_id}'}
    if display:
        ref['display'] = display
    return ref


def _patient_ref(patient):
    return _fhir_ref('Patient', patient.id, patient.user.full_name)


# ---------------------------------------------------------------------------
#  Resource builders
# ---------------------------------------------------------------------------

def fhir_patient(patient):
    user = patient.user
    name = {'family': (user.full_name or '').rsplit(' ', 1)[-1],
            'given': [(user.full_name or '').rsplit(' ', 1)[0]]} if (user.full_name or '').strip() else {}
    gender = patient.gender or None
    if isinstance(gender, str):
        gender = gender.lower()
        if gender.startswith('م'):
            gender = 'female'
        elif gender.startswith('ذ') or gender.startswith('مذكر'):
            gender = 'male'
    resource = {
        'resourceType': 'Patient',
        'id': str(patient.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/Patient']},
        'identifier': [i for i in [
            _ident('urn:oid:1.3.6.1.4.1.99999.iHIS.mrn', patient.mrn, 'MRN'),
            _ident('urn:oid:1.3.6.1.4.1.99999.iHIS.patient-id', patient.id),
        ] if i],
    }
    if name:
        resource['name'] = [name]
    if gender:
        resource['gender'] = gender
    if patient.date_of_birth:
        resource['birthDate'] = _iso(patient.date_of_birth)[:10]
    if patient.blood_type:
        resource['extension'] = [{
            'url': 'http://hl7.org/fhir/StructureDefinition/patient-bloodType',
            'valueCodeableConcept': _codeable(patient.blood_type),
        }]
    telecom = []
    if user.phone:
        telecom.append({'system': 'phone', 'value': user.phone})
    if user.email:
        telecom.append({'system': 'email', 'value': user.email})
    if telecom:
        resource['telecom'] = telecom
    if patient.address:
        resource['address'] = [{'text': patient.address}]
    resource['active'] = bool(user.is_active)
    return resource


def _observation_status(order, result):
    if order.status == 'FINALIZED':
        return 'final'
    if result and result.status == 'Locked':
        return 'final'
    if result and result.status in ('Verified',):
        return 'final'
    if result:
        return 'preliminary'
    return 'registered'


def fhir_observation(order, result=None):
    test = order.test
    coding = _code_ref('urn:oid:1.3.6.1.4.1.99999.iHIS.lab-test', str(test.id), test.test_name)
    status = _observation_status(order, result)
    obs = {
        'resourceType': 'Observation',
        'id': str(order.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/Observation']},
        'status': status,
        'code': {'text': test.test_name, 'coding': [coding]},
        'subject': _patient_ref(order.patient),
        'effectiveDateTime': _iso(result.result_date if result else order.order_date),
        'issued': _iso(result.result_date if result else order.order_date),
    }
    if result:
        if result.qualitative:
            obs['valueString'] = result.qualitative
        elif result.result_value:
            value = {'value': result.result_value}
            unit = result.result_unit or test.unit
            if unit:
                value['unit'] = unit
            if _is_numeric(result.result_value):
                value['value'] = float(result.result_value) if '.' in result.result_value else int(result.result_value)
            obs['valueQuantity'] = value
        interpretation = []
        if result.is_critical:
            interpretation.append({'coding': [_code_ref('http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation', 'A', 'Abnormal')]})
        elif result.is_abnormal:
            interpretation.append({'coding': [_code_ref('http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation', 'A', 'Abnormal')]})
        if interpretation:
            obs['interpretation'] = interpretation
        notes = []
        if result.result_notes:
            notes.append({'text': result.result_notes})
        if notes:
            obs['note'] = notes
    if test.normal_range:
        obs['referenceRange'] = [{'text': test.normal_range}]
    return obs


def _is_numeric(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def fhir_condition(diagnosis, problem=None):
    icd = None
    src = diagnosis or problem
    if diagnosis:
        icd = diagnosis.icd10_code
    elif problem:
        icd = problem.icd10_code
    text = diagnosis.description if diagnosis else problem.description
    status_map = {'Active': 'active', 'Resolved': 'resolved', 'Inactive': 'inactive'}
    if diagnosis:
        clinical_status = 'active'
    else:
        clinical_status = status_map.get(problem.status or 'Active', 'active')
    resource = {
        'resourceType': 'Condition',
        'id': str(src.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/Condition']},
        'clinicalStatus': _codeable(clinical_status,
                                    'http://terminology.hl7.org/CodeSystem/condition-clinical',
                                    clinical_status),
        'code': _codeable(text, 'http://hl7.org/fhir/sid/icd-10', icd) if icd else _codeable(text),
        'subject': _patient_ref(patient_of(src)),
        'recordedDate': _iso(diagnosis.date_diagnosed if diagnosis else problem.created_at),
        'onsetDateTime': _iso(problem.onset if problem else None),
    }
    severity = (problem.severity if problem else None) or getattr(diagnosis, 'severity', None)
    if severity:
        resource['severity'] = _codeable(severity, 'http://terminology.hl7.org/CodeSystem/condition-severity', severity.lower())
    return resource


def patient_of(record):
    from app.models import Diagnosis, Problem
    if isinstance(record, Diagnosis):
        return record.patient
    if isinstance(record, Problem):
        return record.patient
    return None


def fhir_allergy(allergy):
    resource = {
        'resourceType': 'AllergyIntolerance',
        'id': str(allergy.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/AllergyIntolerance']},
        'clinicalStatus': _codeable(allergy.status or 'Active',
                                    'http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical',
                                    (allergy.status or 'Active').lower()),
        'code': _codeable(allergy.substance),
        'patient': _patient_ref(allergy.patient),
        'recordedDate': _iso(allergy.created_at),
    }
    if allergy.reaction:
        resource['reaction'] = [{'manifestation': [_codeable(allergy.reaction)]}]
    if allergy.severity:
        resource['severity'] = allergy.severity.lower()  # mild | moderate | severe
    if allergy.verified:
        resource['verificationStatus'] = _codeable(
            'confirmed', 'http://terminology.hl7.org/CodeSystem/allergyintolerance-verification', 'confirmed')
    return resource


def fhir_immunization(immun):
    resource = {
        'resourceType': 'Immunization',
        'id': str(immun.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/Immunization']},
        'status': 'completed',
        'vaccineCode': _codeable(immun.vaccine_name),
        'patient': _patient_ref(immun.patient),
        'occurrenceDateTime': _iso(immun.administered_at),
        'recorded': _iso(immun.created_at),
    }
    if immun.lot_number:
        resource['lotNumber'] = immun.lot_number
    if immun.site:
        resource['site'] = _codeable(immun.site)
    if immun.dose_number:
        resource['protocolApplied'] = [{'doseNumberPositiveInt': immun.dose_number}]
    if immun.next_due:
        resource['extension'] = [{
            'url': 'urn:oid:1.3.6.1.4.1.99999.iHIS.next-due',
            'valueDate': _iso(immun.next_due)[:10],
        }]
    return resource


def fhir_medication_request(prescription):
    items = list(prescription.items)
    med_names = []
    dosage_instructions = []
    for item in items:
        med_name = None
        if item.medication:
            med_name = item.medication.generic_name or item.medication.brand_name
        if med_name:
            med_names.append(med_name)
        text = ' '.join(x for x in [
            item.dosage, item.frequency, item.duration, item.instructions] if x)
        if text or med_name:
            dosage_instructions.append({'text': (med_name + ' — ' if med_name else '') + text})
    status_map = {'Active': 'active', 'Dispensed': 'active', 'Completed': 'completed',
                  'Cancelled': 'cancelled'}
    resource = {
        'resourceType': 'MedicationRequest',
        'id': str(prescription.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/MedicationRequest']},
        'status': status_map.get(prescription.status or 'Active', 'active'),
        'intent': 'order',
        'medicationCodeableConcept': _codeable(', '.join(med_names) if med_names else 'Medication'),
        'subject': _patient_ref(prescription.patient),
        'authoredOn': _iso(prescription.prescribed_date),
        'dosageInstruction': dosage_instructions,
    }
    return resource


def fhir_encounter_admission(admission):
    period = {'start': _iso(admission.admitted_at)}
    if admission.discharged_at:
        period['end'] = _iso(admission.discharged_at)
    status = 'finished' if admission.status == 'Discharged' else \
        ('planned' if admission.status == 'Scheduled' else 'in-progress')
    enc = {
        'resourceType': 'Encounter',
        'id': 'adm-' + str(admission.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/Encounter']},
        'status': status,
        'class': _codeable('IMP', 'http://terminology.hl7.org/CodeSystem/v3-ActCode', 'inpatient encounter'),
        'type': [_codeable(admission.reason or 'Admission')],
        'subject': _patient_ref(admission.patient),
        'period': period,
    }
    if admission.admitting_doctor_id:
        enc['participant'] = [{'individual': _fhir_ref('Practitioner', admission.admitting_doctor_id)}]
    if admission.admission_no:
        enc['identifier'] = [_ident('urn:oid:1.3.6.1.4.1.99999.iHIS.admission-no', admission.admission_no)]
    if admission.reason:
        enc['reasonCode'] = [_codeable(admission.reason)]
    return enc


def fhir_encounter_appointment(appt):
    enc = {
        'resourceType': 'Encounter',
        'id': 'appt-' + str(appt.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/Encounter']},
        'status': {'Scheduled': 'planned', 'Confirmed': 'planned', 'CheckedIn': 'arrived',
                   'InConsultation': 'in-progress', 'Completed': 'finished',
                   'Cancelled': 'cancelled', 'NoShow': 'cancelled'}.get(appt.status or 'Scheduled', 'planned'),
        'class': _codeable('AMB', 'http://terminology.hl7.org/CodeSystem/v3-ActCode', 'ambulatory'),
        'type': [_codeable(appt.reason or 'Visit')],
        'subject': _patient_ref(appt.patient),
        'period': {'start': _iso(appt.scheduled_at)},
    }
    if appt.doctor_id:
        enc['participant'] = [{'individual': _fhir_ref('Practitioner', appt.doctor_id)}]
    return enc


def fhir_diagnostic_report(order, result=None):
    report = {
        'resourceType': 'DiagnosticReport',
        'id': str(order.id),
        'meta': {'profile': ['http://hl7.org/fhir/StructureDefinition/DiagnosticReport']},
        'status': 'final' if (result and result.status in ('Verified', 'Locked')) or order.status == 'FINALIZED' else 'preliminary',
        'code': {'text': order.test.test_name,
                 'coding': [_code_ref('urn:oid:1.3.6.1.4.1.99999.iHIS.lab-test', str(order.test.id), order.test.test_name)]},
        'subject': _patient_ref(order.patient),
        'effectiveDateTime': _iso(result.result_date if result else order.order_date),
        'issued': _iso(result.result_date if result else order.order_date),
        'result': [_fhir_ref('Observation', str(order.id))],
    }
    if order.accession_number:
        report['identifier'] = [_ident('urn:oid:1.3.6.1.4.1.99999.iHIS.accession', order.accession_number)]
    if result and result.result_notes:
        report['conclusion'] = result.result_notes
    return report


# ---------------------------------------------------------------------------
#  Collection / bundle
# ---------------------------------------------------------------------------

def patient_resources(patient):
    """Every FHIR resource for a patient (the $everything surface)."""
    resources = [fhir_patient(patient)]

    for diag in patient.diagnoses:
        resources.append(fhir_condition(diag))
    for problem in getattr(patient, 'problems', []):
        resources.append(fhir_condition(None, problem))
    for allergy in patient.allergy_list:
        resources.append(fhir_allergy(allergy))
    for immun in patient.immunizations:
        resources.append(fhir_immunization(immun))
    for rx in patient.prescriptions:
        resources.append(fhir_medication_request(rx))
    for order in patient.lab_orders:
        result = order.result
        if result:
            resources.append(fhir_observation(order, result))
        resources.append(fhir_diagnostic_report(order, result))
    for admission in patient.admissions:
        if admission.status in ('Admitted', 'Discharged'):
            resources.append(fhir_encounter_admission(admission))
    for appt in patient.appointments:
        resources.append(fhir_encounter_appointment(appt))
    return resources


def patient_everything(patient):
    entries = patient_resources(patient)
    return {
        'resourceType': 'Bundle',
        'id': str(uuid.uuid4()),
        'type': 'searchset',
        'meta': {'lastUpdated': _iso(utcnow())},
        'total': len(entries),
        'entry': [{'fullUrl': f'urn:uuid:{uuid.uuid4()}',
                   'resource': r} for r in entries],
    }