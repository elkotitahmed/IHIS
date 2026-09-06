"""Medication reconciliation engine.

Compares a patient's home medication list against their active prescriptions
(and, when the prescribing context provides it, a separated prescription list)
to surface omissions, duplicates, new orders, dose discrepancies, drug-drug
interactions, and allergy conflicts. Each finding is stored as a
ReconciliationDiscrepancy on a MedicationReconciliation record so it can be
reviewed and resolved without mutating the original prescriptions.
"""
import json

from app import db
from app.models import (Allergy, DrugInteraction, Medication, Patient,
                        MedicationReconciliation, ReconciliationDiscrepancy)
from app.utils import utcnow

VALID_TYPES = ('Admission', 'Transfer', 'Discharge', 'Ambulatory')
VALID_DISCREPANCY_TYPES = (
    'DUPLICATE', 'OMISSION', 'DISCREPANCY', 'NEW_ORDER',
    'DOSE_DIFFERENCE', 'INTERACTION', 'ALLERGY',
)


def normalize_home_medications(raw):
    """Accept a list of {medication_name, dose, frequency} dicts (or a JSON
    string of them) and return the normalized list."""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append({
                'medication_name': (item.get('medication_name') or
                                    item.get('name') or '').strip(),
                'dose': (item.get('dose') or item.get('dosage') or '').strip(),
                'frequency': (item.get('frequency') or '').strip(),
            })
        elif isinstance(item, str) and item.strip():
            out.append({'medication_name': item.strip(), 'dose': '', 'frequency': ''})
    return [x for x in out if x['medication_name']]


def resolve_medication(name):
    """Best-effort lookup of a Medication by generic or brand name."""
    if not name:
        return None
    name = name.strip()
    med = (Medication.query
           .filter(db.or_(Medication.generic_name.ilike(name),
                          Medication.brand_name.ilike(name)))
           .first())
    if med:
        return med
    like = f'%{name}%'
    return (Medication.query
            .filter(db.or_(Medication.generic_name.ilike(like),
                           Medication.brand_name.ilike(like)))
            .first())


def active_prescriptions(patient_id):
    """Summarize currently-active prescription items for a patient as
    ``[{'medication', 'dose', 'frequency', 'prescription'}]``."""
    from app.models import Prescription
    from app.models import PrescriptionItem
    items = (PrescriptionItem.query
             .join(Prescription, PrescriptionItem.prescription_id == Prescription.id)
             .filter(Prescription.patient_id == patient_id,
                     Prescription.status.in_(('Active', 'Partially Dispensed')),
                     PrescriptionItem.status == 'Active')
             .all())
    out = []
    for it in items:
        med = it.medication
        name = med.generic_name if med else None
        out.append({'medication': it.medication, 'name': name,
                    'dose': it.dosage, 'frequency': it.frequency,
                    'prescription': it.prescription})
    return out


def _patient_allergy_names(patient_id):
    subs = {a.substance.strip().lower() for a in
            Allergy.query.filter_by(patient_id=patient_id,
                                    status='Active').all()}
    demos = []
    pat = db.session.get(Patient, patient_id)
    if pat and pat.allergies:
        demos = [x.strip().lower() for x in pat.allergies.split(',') if x.strip()]
    return subs | set(demos)


def _drug_pairs(active):
    """All unordered pairs among active medication rows with their resolved
    Medication instances."""
    meds = [entry['medication'] for entry in active if entry.get('medication')]
    pairs = []
    for i, a in enumerate(meds):
        for b in meds[i + 1:]:
            pairs.append((a, b))
    return pairs


def _interactions_among(meds):
    """Map of frozenset({med_a_id, med_b_id}) -> DrugInteraction for every
    documented interaction among ``meds`` — a single query instead of one per
    pair (which was O(n²) round-trips on long medication lists)."""
    ids = {m.id for m in meds if m is not None}
    if len(ids) < 2:
        return {}
    rows = (DrugInteraction.query
            .filter(DrugInteraction.medication_a_id.in_(ids),
                    DrugInteraction.medication_b_id.in_(ids)).all())
    found = {}
    for row in rows:
        found.setdefault(frozenset((row.medication_a_id, row.medication_b_id)), row)
    return found


def _interaction_for_pair(a, b, lookup=None):
    if lookup is None:
        lookup = _interactions_among([a, b])
    return lookup.get(frozenset((a.id, b.id)))


def run_reconciliation(patient_id, pharmacist_id=None, home_medications=None,
                       admission_id=None, reconciliation_type='Admission',
                       summary=None):
    """Create a MedicationReconciliation and its discrepancy findings.

    Returns ``(reconciliation, discrepancies)``. Callers are responsible for
    committing the session (the engine flushes for ids but does not commit).
    """
    if reconciliation_type not in VALID_TYPES:
        reconciliation_type = 'Admission'
    home = normalize_home_medications(home_medications)
    active = active_prescriptions(patient_id)

    reconciliation = MedicationReconciliation(
        patient_id=patient_id,
        pharmacist_id=pharmacist_id,
        admission_id=admission_id,
        reconciliation_type=reconciliation_type,
        home_medications=json.dumps(home, ensure_ascii=False),
        active_prescriptions=json.dumps(
            [{'name': e['name'], 'dose': e['dose'], 'frequency': e['frequency']}
             for e in active], ensure_ascii=False),
        summary=summary or '',
        status='Open',
    )
    db.session.add(reconciliation)
    db.session.flush()

    discrepancies = _find_discrepancies(reconciliation.id, patient_id, home, active)
    for d in discrepancies:
        db.session.add(d)
    db.session.flush()
    return reconciliation, discrepancies


def complete_reconciliation(reconciliation, notes=None):
    reconciliation.status = 'Completed'
    reconciliation.notes = (notes or reconciliation.notes or '')
    reconciliation.completed_at = utcnow()
    return reconciliation


def _find_discrepancies(reconciliation_id, patient_id, home, active):
    findings = []
    active_by_name = {}
    for entry in active:
        key = (entry['name'] or '').strip().lower()
        if key:
            active_by_name.setdefault(key, []).append(entry)

    # --- Omissions: home medication with no matching active prescription ---
    for h in home:
        h_name = h['medication_name'].strip().lower()
        found = active_by_name.get(h_name)
        if not found:
            found = _fuzzy_match_active(h_name, active_by_name)
        if not found:
            findings.append(_disc(
                reconciliation_id, None, 'OMISSION',
                f"Home medication '{h['medication_name']}' has no active prescription.",
                'MODERATE', 'Re-add or confirm intentional discontinuation.'))

    # --- New orders / duplicates / dose differences ---------------------
    home_by_name = {h['medication_name'].strip().lower(): h for h in home}
    seen = set()
    for key, entries in active_by_name.items():
        if key in seen:
            continue
        seen.add(key)
        h = home_by_name.get(key)
        if h is None:
            h = _fuzzy_match_home(key, home_by_name)
        if h is None:
            findings.append(_disc(
                reconciliation_id, entries[0]['medication'].id if entries[0].get('medication') else None,
                'NEW_ORDER',
                f"Prescribed '{entries[0]['name']}' is not on the home list. Confirm it is new.",
                'LOW', 'Confirm with prescriber if unintended.'))
        elif len(entries) > 1:
            findings.append(_disc(
                reconciliation_id, entries[0]['medication'].id if entries[0].get('medication') else None,
                'DUPLICATE',
                f"Harmonized duplicate therapy: '{entries[0]['name']}' appears {len(entries)} times.",
                'MODERATE', 'Consolidate to a single regimen.'))
        elif h['dose'] and entries[0]['dose'] and h['dose'].strip().lower() != entries[0]['dose'].strip().lower():
            findings.append(_disc(
                reconciliation_id, entries[0]['medication'].id if entries[0].get('medication') else None,
                'DOSE_DIFFERENCE',
                f"Home dose {h['dose']} vs prescribed {entries[0]['dose']} for "
                f"'{entries[0]['name']}'.",
                'LOW', 'Verify the intended strength.'))

    # --- Drug-drug interactions among active meds ------------------------
    lookup = _interactions_among([e['medication'] for e in active if e.get('medication')])
    for a, b in _drug_pairs(active):
        interaction = _interaction_for_pair(a, b, lookup)
        if interaction is None:
            continue
        severity = _SEVERITY_MAP.get((interaction.severity or '').strip().lower(), 'MODERATE')
        findings.append(_disc(
            reconciliation_id, a.id, 'INTERACTION',
            f"Interaction '{interaction.label}': {interaction.description or ''}",
            severity, interaction.management or 'Notify the prescriber.'))

    # --- Allergy conflicts ------------------------------------------------
    allergy_names = _patient_allergy_names(patient_id)
    for entry in active:
        med = entry.get('medication')
        if not med:
            continue
        med_name = (med.generic_name or '').strip().lower()
        med_brand = (med.brand_name or '').strip().lower()
        conflicting = (med_name in allergy_names) or (med_brand in allergy_names)
        if not conflicting and med_name:
            # Substring matching only for reasonably specific names, so an
            # allergy recorded as "ASA" or "nut" cannot flag unrelated drugs.
            conflicting = any((allerg in med_name or med_name in allerg)
                              for allerg in allergy_names
                              if allerg and len(allerg) >= 4 and len(med_name) >= 4)
        if conflicting:
            findings.append(_disc(
                reconciliation_id, med.id, 'ALLERGY',
                f"Potential allergy conflict: '{med.generic_name}' matches patient allergy.",
                'HIGH', 'Stop dose; consult prescriber before continuing.'))

    return findings


# Interaction rows use Minor/Moderate/Major/Contraindicated; discrepancy rows
# use LOW/MODERATE/HIGH/CRITICAL (what the reconciliation UI colours by).
_SEVERITY_MAP = {
    'minor': 'LOW', 'low': 'LOW',
    'moderate': 'MODERATE', 'medium': 'MODERATE',
    'major': 'HIGH', 'high': 'HIGH', 'severe': 'HIGH',
    'contraindicated': 'CRITICAL', 'critical': 'CRITICAL',
}


def _disc(reconciliation_id, medication_id, dtype, description,
          severity='MODERATE', recommended_action=''):
    if dtype not in VALID_DISCREPANCY_TYPES:
        dtype = 'DISCREPANCY'
    severity = _SEVERITY_MAP.get((severity or '').strip().lower(), 'MODERATE')
    return ReconciliationDiscrepancy(
        reconciliation_id=reconciliation_id,
        medication_id=medication_id,
        discrepancy_type=dtype,
        description=description,
        severity=severity,
        recommended_action=recommended_action,
        status='Open',
    )


def _fuzzy_match_active(name, active_by_name):
    for key in active_by_name:
        if len(name) >= 4 and (name in key or key in name):
            return active_by_name[key]
    return None


def _fuzzy_match_home(key, home_by_name):
    for h_key in home_by_name:
        if len(key) >= 4 and (key in h_key or h_key in key):
            return home_by_name[h_key]
    return None