"""Radiation Dose Service — calculates, summarizes, and alerts on
patient radiation exposure from imaging dose records.

Design principles:
- Does NOT fabricate dose values
- Distinguishes measured vs estimated doses
- Groups by modality, body region, and year
- Compares against institutional reference levels
- Maintains calculation provenance
"""
from datetime import datetime, date

from app.utils import utcnow
from sqlalchemy import func
from sqlalchemy import extract as sa_extract, and_

from app import db
from app.models import (ImagingDoseRecord, ImagingReferenceLevel,
                        PatientImagingSafetyProfile, RadiologyOrder,
                        ImagingType)

# Approximate effective dose (mSv) per exam type for estimation when only
# raw metrics are available.  These are ORDER-OF-MAGNITUDE reference values
# from ICRP 103 / AAPM reports — used ONLY for estimation, never presented
# as measured values.
_EFFECTIVE_DOSE_ESTIMATES = {
    'CT': 7.0,            # typical CT scan
    'CT Head': 2.0,
    'CT Chest': 7.0,
    'CT Abdomen': 10.0,
    'CT Pelvis': 6.0,
    'CT Spine': 6.0,
    'X-ray': 0.1,
    'X-ray Chest': 0.02,
    'X-ray Spine': 1.5,
    'X-ray Limb': 0.001,
    'Fluoroscopy': 5.0,
    'Mammography': 0.4,
    'Nuclear Medicine': 6.0,
    'PET': 14.0,
    'PET-CT': 20.0,
    'Angiography': 5.0,
}


class RadiationDoseService:
    """Service for calculating and managing patient radiation exposure."""

    def get_patient_dose_records(self, patient_id, year=None):
        """Return dose records for a patient, optionally filtered by year."""
        q = ImagingDoseRecord.query.filter_by(patient_id=patient_id)
        if year:
            q = q.filter(sa_extract('year', ImagingDoseRecord.study_date) == int(year))
        return q.order_by(ImagingDoseRecord.study_date.desc()).all()

    def get_patient_annual_summary(self, patient_id, year=None):
        """Build an annual radiation exposure summary grouped by modality.
        Returns dict with per-modality totals and overall summary."""
        year = year or date.today().year
        records = self.get_patient_dose_records(patient_id, year)

        if not records:
            return {
                'year': year,
                'modalities': {},
                'total_effective_dose': None,
                'total_effective_dose_type': 'Unavailable',
                'total_studies': 0,
                'radiation_studies': 0,
                'non_radiation_studies': 0,
                'studies_missing_dose': 0,
                'message': 'No imaging records found for this year.',
            }

        NON_RADIATION = {'MRI', 'Ultrasound'}
        by_modality = {}
        total_effective = 0.0
        total_effective_is_estimated = False
        total_effective_is_measured = False
        studies_missing = 0
        radiation_count = 0
        non_radiation_count = 0

        for r in records:
            mod = r.modality or 'Unknown'
            if mod in NON_RADIATION:
                non_radiation_count += 1
                continue
            radiation_count += 1

            if mod not in by_modality:
                by_modality[mod] = {
                    'count': 0,
                    'total_dlp': 0,
                    'total_ctdi': 0,
                    'total_dap': 0,
                    'total_effective': 0,
                    'has_measured_dose': False,
                    'has_estimated_dose': False,
                    'missing_dose_count': 0,
                    'records': [],
                }
            m = by_modality[mod]
            m['count'] += 1
            m['records'].append(r)

            has_any_dose = False
            if r.dlp:
                m['total_dlp'] += r.dlp
                has_any_dose = True
            if r.ctdi_vol:
                m['total_ctdi'] += r.ctdi_vol
                has_any_dose = True
            if r.dap:
                m['total_dap'] += r.dap
                has_any_dose = True
            if r.dose_value and r.dose_unit == 'mSv':
                if r.is_estimated:
                    m['total_effective'] += r.dose_value
                    m['has_estimated_dose'] = True
                    total_effective_is_estimated = True
                else:
                    m['total_effective'] += r.dose_value
                    m['has_measured_dose'] = True
                    total_effective_is_measured = True
                has_any_dose = True
            elif r.effective_dose_est:
                m['total_effective'] += r.effective_dose_est
                m['has_estimated_dose'] = True
                total_effective_is_estimated = True
                has_any_dose = True

            if not has_any_dose:
                m['missing_dose_count'] += 1
                studies_missing += 1

        # Sum once per modality (summing inside the loop re-added the running
        # total on every record and inflated the annual dose).
        total_effective = sum(m['total_effective'] for m in by_modality.values())

        # Determine overall dose status
        if total_effective_is_measured and not total_effective_is_estimated:
            dose_type = 'Measured'
        elif total_effective_is_estimated and not total_effective_is_measured:
            dose_type = 'Estimated'
        elif total_effective_is_measured and total_effective_is_estimated:
            dose_type = 'Partially Available'
        else:
            dose_type = 'Unavailable'

        return {
            'year': year,
            'modalities': by_modality,
            'total_effective_dose': round(total_effective, 2) if dose_type != 'Unavailable' else None,
            'total_effective_dose_type': dose_type,
            'total_studies': len(records),
            'radiation_studies': radiation_count,
            'non_radiation_studies': non_radiation_count,
            'studies_missing_dose': studies_missing,
        }

    def get_patient_cumulative_summary(self, patient_id):
        """Calculate cumulative dose across all years."""
        records = ImagingDoseRecord.query.filter_by(
            patient_id=patient_id).order_by(
            ImagingDoseRecord.study_date.asc()).all()

        NON_RADIATION = {'MRI', 'Ultrasound'}
        by_year = {}
        cumulative_effective = 0.0
        has_any_dose = False

        for r in records:
            year = r.study_date.year if r.study_date else 'Unknown'
            if r.modality in NON_RADIATION:
                continue
            if year not in by_year:
                by_year[year] = {'count': 0, 'total_effective': 0}
            by_year[year]['count'] += 1

            effective = r.effective_dose_est or (
                r.dose_value if r.dose_unit == 'mSv' else None)
            if effective:
                by_year[year]['total_effective'] += effective
                cumulative_effective += effective
                has_any_dose = True

        return {
            'cumulative_effective_dose': round(cumulative_effective, 2) if has_any_dose else None,
            'dose_available': has_any_dose,
            'by_year': by_year,
            'total_records': len([r for r in records if r.modality not in NON_RADIATION]),
        }

    def compare_with_reference_levels(self, patient_id, modality=None):
        """Compare patient's exposure against institutional reference levels."""
        threshold = ImagingReferenceLevel.query.filter_by(
            is_active=True)
        if modality:
            threshold = threshold.filter_by(modality=modality)
        threshold = threshold.first()

        if not threshold:
            return {
                'configured': False,
                'message': 'No institutional reference threshold configured.',
            }

        annual = self.get_patient_annual_summary(patient_id)
        mod_data = annual['modalities'].get(modality, {}) if modality else {}

        alerts = []
        if threshold.effective_dose_threshold_msv and annual.get('total_effective_dose'):
            if annual['total_effective_dose'] > threshold.effective_dose_threshold_msv:
                alerts.append({
                    'type': 'dose_review',
                    'severity': 'Review Required',
                    'message': (f'Recorded cumulative dose ({annual["total_effective_dose"]} mSv) '
                                f'exceeds the institutional reference threshold '
                                f'({threshold.effective_dose_threshold_msv} mSv). '
                                f'Review justification and available prior imaging.'),
                })

        if threshold.dlp_threshold_mgycm and mod_data.get('total_dlp'):
            if mod_data['total_dlp'] > threshold.dlp_threshold_mgycm:
                alerts.append({
                    'type': 'dose_review',
                    'severity': 'Review Required',
                    'message': (f'{modality} DLP ({mod_data["total_dlp"]} mGy·cm) '
                                f'exceeds reference threshold '
                                f'({threshold.dlp_threshold_mgycm} mGy·cm).'),
                })

        return {
            'configured': True,
            'threshold': {
                'name': threshold.name,
                'effective_dose': threshold.effective_dose_threshold_msv,
                'dlp': threshold.dlp_threshold_mgycm,
                'ctdi': threshold.ctdi_threshold_mgy,
            },
            'alerts': alerts,
        }

    def generate_dose_alerts(self, patient_id):
        """Generate informational and safety alerts based on dose history."""
        annual = self.get_patient_annual_summary(patient_id)
        alerts = []

        # Check for multiple recent CT exams
        recent_ct = ImagingDoseRecord.query.filter(
            ImagingDoseRecord.patient_id == patient_id,
            ImagingDoseRecord.modality == 'CT',
            ImagingDoseRecord.study_date >= utcnow().replace(
                month=1, day=1)).count()
        if recent_ct >= 2:
            alerts.append({
                'severity': 'Informational',
                'category': 'radiation',
                'title': f'{recent_ct} CT Examinations This Year',
                'message': (f'Patient has undergone {recent_ct} CT examinations '
                            f'during the current calendar year.'),
            })

        # Check for very recent duplicate CT
        from datetime import timedelta
        recent_ct_orders = RadiologyOrder.query.filter(
            RadiologyOrder.patient_id == patient_id,
            RadiologyOrder.status.in_(['Performed', 'Reported', 'Signed', 'Finalized']),
        ).join(ImagingType).filter(
            ImagingType.name.ilike('%CT%')
        ).order_by(RadiologyOrder.order_date.desc()).limit(5).all()

        if len(recent_ct_orders) >= 2:
            last_two = recent_ct_orders[:2]
            if last_two[0].order_date and last_two[1].order_date:
                gap = (last_two[0].order_date - last_two[1].order_date).days
                if gap <= 30:
                    alerts.append({
                        'severity': 'Safety Review',
                        'category': 'radiation',
                        'title': 'Multiple Recent CT Examinations',
                        'message': (f'Multiple recent CT examinations detected '
                                    f'({gap} days apart). Consider reviewing previous '
                                    f'imaging before ordering another examination.'),
                    })

        # Compare with reference levels
        ref_alerts = self.compare_with_reference_levels(patient_id)
        for a in ref_alerts.get('alerts', []):
            alerts.append({
                'severity': a['severity'],
                'category': 'radiation',
                'title': 'Dose Reference Level Alert',
                'message': a['message'],
            })

        return alerts

    def check_duplicate_imaging(self, patient_id, modality, body_region,
                                days=30):
        """Check for duplicate/recent imaging of the same type."""
        from datetime import timedelta
        cutoff = utcnow() - timedelta(days=days)
        recent = RadiologyOrder.query.filter(
            RadiologyOrder.patient_id == patient_id,
            RadiologyOrder.order_date >= cutoff,
            RadiologyOrder.status.notin_(['Cancelled']),
        ).join(ImagingType).filter(
            ImagingType.name.ilike(f'%{modality}%')
        ).order_by(RadiologyOrder.order_date.desc()).all()

        if recent:
            latest = recent[0]
            return {
                'duplicate_found': True,
                'previous_order': latest,
                'days_ago': (utcnow() - latest.order_date).days if latest.order_date else None,
                'study_type': latest.imaging_type.name if latest.imaging_type else modality,
                'message': (f'A {latest.imaging_type.name if latest.imaging_type else modality} '
                            f'was performed {self._days_ago_text(latest.order_date)} ago.'),
            }
        return {'duplicate_found': False}

    def _days_ago_text(self, dt):
        if not dt:
            return 'previously'
        days = (utcnow() - dt).days
        if days == 0:
            return 'today'
        elif days == 1:
            return 'yesterday'
        else:
            return f'{days} days'

    def record_dose(self, patient_id, order_id, **kwargs):
        """Create a dose record. Only stores provided values — never invents."""
        record = ImagingDoseRecord(
            patient_id=patient_id,
            order_id=order_id,
            **{k: v for k, v in kwargs.items() if v is not None})
        db.session.add(record)
        db.session.commit()
        return record

    def correct_dose_record(self, record_id, new_value, reason, user_id):
        """Correct a dose record while preserving the original."""
        record = db.session.get(ImagingDoseRecord, record_id)
        if not record:
            return None
        record.original_value = record.dose_value
        record.dose_value = new_value
        record.is_corrected = True
        record.correction_reason = reason
        record.corrected_by = user_id
        record.corrected_at = utcnow()
        db.session.commit()
        return record
