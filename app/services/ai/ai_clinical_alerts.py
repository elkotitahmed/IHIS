"""AI Clinical Alert Generator.

Monitors patient data and generates proactive clinical alerts:
- Critical lab values
- Deteriorating vital signs
- Drug interactions / contraindications
- Readmission risk
- Sepsis screening
"""
from datetime import datetime, timedelta
from app import db
from app.models import (Patient, ClinicalAlert, VitalSign, LabResult, LabOrder,
                        Prescription, Medication, DrugInteraction, Admission,
                        Diagnosis)
from app.services.ai.gemini_base import GeminiBase, _collect_patient_context


class AIClinicalAlertEngine:
    """Generates clinical alerts from patient data (rules + optional LLM)."""

    def __init__(self):
        self.gemini = GeminiBase(
            system_prompt=(
                "You are a clinical early warning system. Analyze the patient "
                "data and determine if any clinical alerts should be generated. "
                "For each alert, provide severity (Critical/High/Moderate/Low), "
                "category, title, message, and recommended action."
            ),
            temperature=0.2, max_tokens=2000)

    def scan_patient(self, patient_id):
        """Run all alert rules for a patient, return list of alert dicts."""
        alerts = []
        alerts.extend(self._check_critical_labs(patient_id))
        alerts.extend(self._check_vital_trends(patient_id))
        alerts.extend(self._check_drug_interactions(patient_id))
        alerts.extend(self._check_readmission_risk(patient_id))
        return alerts

    def scan_all_active_patients(self):
        """Scan all patients with recent activity for alerts."""
        cutoff = datetime.now() - timedelta(days=7)
        patient_ids = set()
        for order in LabOrder.query.filter(
                LabOrder.order_date >= cutoff).all():
            patient_ids.add(order.patient_id)
        for rx in Prescription.query.filter_by(status='Active').all():
            patient_ids.add(rx.patient_id)
        for adm in Admission.query.filter_by(status='Admitted').all():
            patient_ids.add(adm.patient_id)

        all_alerts = []
        for pid in patient_ids:
            all_alerts.extend(self.scan_patient(pid))
        return all_alerts

    def _check_critical_labs(self, patient_id):
        alerts = []
        recent_results = LabResult.query.join(LabOrder).filter(
            LabOrder.patient_id == patient_id,
            LabResult.is_abnormal.is_(True),
            LabResult.resulted_at >= datetime.now() - timedelta(hours=48)
        ).all()
        for r in recent_results:
            order = LabOrder.query.get(r.order_id)
            test_name = order.test.test_name if order and order.test else 'Lab test'
            # Check for truly critical values
            is_critical = self._is_critical_value(r, order)
            if is_critical:
                alerts.append({
                    'severity': 'Critical',
                    'category': 'lab_value',
                    'title': f'Critical Lab: {test_name}',
                    'message': (f'{test_name}: {r.result_value} '
                                f'{order.test.unit if order and order.test else ""} '
                                f'(ABNORMAL). Requires immediate clinical review.'),
                    'patient_id': patient_id,
                    'action': 'Review result immediately and consider intervention.',
                })
        return alerts

    def _check_vital_trends(self, patient_id):
        alerts = []
        recent_vitals = VitalSign.query.filter_by(
            patient_id=patient_id).order_by(
            VitalSign.recorded_at.desc()).limit(5).all()
        if len(recent_vitals) < 2:
            return alerts

        # Check for deterioration patterns
        latest = recent_vitals[0]
        if latest.heart_rate and latest.heart_rate > 120:
            alerts.append({
                'severity': 'High',
                'category': 'vital_signs',
                'title': 'Tachycardia Detected',
                'message': f'Heart rate {latest.heart_rate} bpm. '
                           f'Evaluate for sepsis, pain, bleeding, or cardiac cause.',
                'patient_id': patient_id,
                'action': 'Assess patient and consider cardiac workup.',
            })
        if latest.blood_pressure_systolic and latest.blood_pressure_systolic >= 180:
            alerts.append({
                'severity': 'Critical',
                'category': 'vital_signs',
                'title': 'Hypertensive Emergency',
                'message': f'BP {latest.blood_pressure_systolic}/{latest.blood_pressure_diastolic}. '
                           f'Immediate evaluation required.',
                'patient_id': patient_id,
                'action': 'Immediate BP management per protocol.',
            })
        if latest.oxygen_saturation is not None and latest.oxygen_saturation < 90:
            alerts.append({
                'severity': 'Critical',
                'category': 'vital_signs',
                'title': 'Critical Oxygen Saturation',
                'message': f'SpO2 {latest.oxygen_saturation}%. '
                           f'Evaluate for respiratory failure.',
                'patient_id': patient_id,
                'action': 'Supplemental O2 and respiratory assessment.',
            })
        if latest.temperature and latest.temperature >= 39.5:
            alerts.append({
                'severity': 'High',
                'category': 'vital_signs',
                'title': 'High Fever',
                'message': f'Temperature {latest.temperature}°C. '
                           f'Evaluate for infection source.',
                'patient_id': patient_id,
                'action': 'Blood cultures, infection workup, antipyretics.',
            })
        return alerts

    def _check_drug_interactions(self, patient_id):
        alerts = []
        active_rx = Prescription.query.filter_by(
            patient_id=patient_id, status='Active').all()
        med_ids = []
        for rx in active_rx:
            for item in rx.items:
                if item.medication_id:
                    med_ids.append(item.medication_id)

        if len(med_ids) < 2:
            return alerts

        # Check DrugInteraction table
        interactions = DrugInteraction.query.filter(
            DrugInteraction.medication_a_id.in_(med_ids),
            DrugInteraction.medication_b_id.in_(med_ids)
        ).all()
        for inter in interactions:
            if inter.severity in ('Severe', 'Major', 'Contraindicated'):
                med_a = Medication.query.get(inter.medication_a_id)
                med_b = Medication.query.get(inter.medication_b_id)
                alerts.append({
                    'severity': 'High' if inter.severity != 'Contraindicated' else 'Critical',
                    'category': 'drug_interaction',
                    'title': f'Drug Interaction: {inter.severity}',
                    'message': (f'{med_a.generic_name if med_a else "?"} + '
                                f'{med_b.generic_name if med_b else "?"}: '
                                f'{inter.description or "Significant interaction detected"}'),
                    'patient_id': patient_id,
                    'action': 'Review medications and consider alternatives.',
                })
        return alerts

    def _check_readmission_risk(self, patient_id):
        alerts = []
        recent_discharge = Admission.query.filter(
            Admission.patient_id == patient_id,
            Admission.status.in_(['Discharged', 'Completed'])
        ).order_by(Admission.discharged_at.desc()).first()

        if recent_discharge and recent_discharge.discharged_at:
            days_since = (datetime.now().date() - recent_discharge.discharged_at.date()).days
            if days_since <= 30:
                alerts.append({
                    'severity': 'Moderate',
                    'category': 'readmission_risk',
                    'title': '30-Day Readmission Risk',
                    'message': (f'Patient was discharged {days_since} days ago '
                                f'({recent_discharge.reason or "N/A"}). '
                                f'Monitor closely for readmission risk factors.'),
                    'patient_id': patient_id,
                    'action': 'Ensure follow-up appointment and care transition.',
                })
        return alerts

    def _is_critical_value(self, result, order):
        """Determine if a lab result is truly critical."""
        if not order or not order.test:
            return False
        test_name = (order.test.test_name or '').lower()
        try:
            val = float(result.result_value)
        except (ValueError, TypeError):
            return False

        critical_ranges = {
            'glucose': (40, 500),
            'potassium': (2.5, 6.5),
            'sodium': (120, 160),
            'hemoglobin': (5, 20),
            'platelets': (20, 50),
            'wbc': (1, 30),
            'creatinine': (0.1, 10),
            'inr': (0.5, 5),
            'lactate': (0.5, 6),
            'troponin': (0, 0.1),
        }
        for keyword, (low, high) in critical_ranges.items():
            if keyword in test_name:
                return val < low or val > high
        return False

    def create_alert(self, alert_dict):
        """Persist a ClinicalAlert record."""
        try:
            alert = ClinicalAlert(
                patient_id=alert_dict['patient_id'],
                alert_type=alert_dict.get('category', 'general'),
                severity=alert_dict.get('severity', 'Moderate'),
                title=alert_dict.get('title', 'Clinical Alert'),
                message=alert_dict.get('message', ''),
            )
            db.session.add(alert)
            db.session.commit()
            return alert
        except Exception:
            db.session.rollback()
            return None
