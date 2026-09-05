"""Radiology Safety Service — evaluates CT/MRI/contrast safety,
identifies missing screening, and generates clinical warnings.

Design principles:
- Decision SUPPORT only — does not replace clinical judgment
- Never automatically denies an examination
- Clearly labels AI-generated vs rule-based warnings
- Maintains distinction between ionizing and non-ionizing radiation
"""
from datetime import datetime, date

from app import db
from app.models import (Patient, PatientImagingSafetyProfile, MRIImplantRegistry,
                        ImagingSafetyScreening, RadiologyOrder, ImagingType,
                        ContrastAdministration, LabResult, LabOrder, Diagnosis)

# MRI-unsafe device categories (known to be contraindicated in most cases)
MR_UNSAFE_CATEGORIES = {
    'pacemaker', 'icd', 'neurostimulator', 'cochlear implant',
    'implanted infusion pump',
}

# MR Conditional — requires verification of specific model
MR_CONDITIONAL_CATEGORIES = {
    'vascular clips', 'orthopedic hardware', 'metallic implant',
    'foreign body', 'retained fragment',
}


class RadiologySafetyService:
    """Evaluates imaging safety for patients."""

    def get_or_create_safety_profile(self, patient_id):
        """Get or create the patient's imaging safety profile."""
        profile = PatientImagingSafetyProfile.query.filter_by(
            patient_id=patient_id).first()
        if not profile:
            profile = PatientImagingSafetyProfile(patient_id=patient_id)
            db.session.add(profile)
            db.session.commit()
        return profile

    def evaluate_ct_safety(self, patient_id, order_id=None):
        """Evaluate CT-specific safety considerations."""
        profile = self.get_or_create_safety_profile(patient_id)
        warnings = []

        # Previous contrast reaction
        if profile.previous_contrast_reaction:
            warnings.append({
                'severity': 'Important',
                'category': 'contrast',
                'title': 'Previous Contrast Reaction Documented',
                'message': (f'Patient has a documented previous contrast reaction: '
                            f'{profile.contrast_reaction_details or "Details not recorded"}. '
                            f'Consider pre-medication protocol.'),
            })

        # Renal function
        if profile.last_egfr is not None:
            if profile.last_egfr < 30:
                warnings.append({
                    'severity': 'Critical',
                    'category': 'renal',
                    'title': 'Severe Renal Impairment',
                    'message': (f'eGFR: {profile.last_egfr} mL/min — '
                                f'Iodinated contrast may be contraindicated. '
                                f'Review nephrology consultation.'),
                })
            elif profile.last_egfr < 45:
                warnings.append({
                    'severity': 'Important',
                    'category': 'renal',
                    'title': 'Moderate Renal Impairment',
                    'message': (f'eGFR: {profile.last_egfr} mL/min — '
                                f'Consider hydration protocol and lowest acceptable contrast dose.'),
                })
        else:
            warnings.append({
                'severity': 'Review Required',
                'category': 'renal',
                'title': 'Renal Function Unknown',
                'message': 'No recent renal function results available. Consider checking creatinine/eGFR before contrast administration.',
            })

        # Pregnancy
        if profile.pregnancy_status in ('Pregnant', 'Possibly Pregnant'):
            warnings.append({
                'severity': 'Critical',
                'category': 'pregnancy',
                'title': 'Pregnancy Status',
                'message': ('Patient is pregnant or possibly pregnant. '
                            'CT involves ionizing radiation. '
                            'Review risk-benefit and consider alternative imaging.'),
            })

        # Allergies
        from app.models import Patient as PatientModel
        patient = db.session.get(PatientModel, patient_id)
        if patient and patient.allergies:
            warnings.append({
                'severity': 'Informational',
                'category': 'allergy',
                'title': 'Documented Allergies',
                'message': f'Patient allergies: {patient.allergies}',
            })

        # CT-specific contraindications
        if profile.ct_contraindications:
            warnings.append({
                'severity': 'Important',
                'category': 'ct_precaution',
                'title': 'CT Contraindications/Precautions',
                'message': profile.ct_contraindications,
            })

        return {
            'safety_status': 'Cleared' if not warnings else 'Requires Review',
            'warnings': warnings,
            'profile': {
                'contrast_reaction': profile.previous_contrast_reaction,
                'egfr': profile.last_egfr,
                'creatinine': profile.last_creatinine,
                'renal_date': profile.renal_function_date,
                'pregnancy': profile.pregnancy_status,
            },
        }

    def evaluate_mri_safety(self, patient_id, order_id=None):
        """Evaluate MRI-specific safety screening."""
        profile = self.get_or_create_safety_profile(patient_id)
        implants = MRIImplantRegistry.query.filter_by(
            patient_id=patient_id, is_active=True).all()
        warnings = []

        # Check MRI screening status
        if profile.mri_screening_status == 'Not Screened':
            warnings.append({
                'severity': 'Critical',
                'category': 'screening',
                'title': 'MRI Safety Screening Incomplete',
                'message': 'MRI safety screening has not been completed for this patient.',
            })

        # Evaluate implants
        has_unknown = False
        has_unsafe = False
        has_conditional = False

        for implant in implants:
            if implant.mr_safety_class == 'MR Unsafe':
                has_unsafe = True
                warnings.append({
                    'severity': 'Critical',
                    'category': 'implant',
                    'title': f'MR Unsafe Device: {implant.device_name}',
                    'message': (f'{implant.device_name} ({implant.manufacturer or ""} '
                                f'{implant.model_number or ""}) is classified as MR Unsafe.'),
                })
            elif implant.mr_safety_class == 'MR Conditional':
                has_conditional = True
                warnings.append({
                    'severity': 'Important',
                    'category': 'implant',
                    'title': f'MR Conditional Device: {implant.device_name}',
                    'message': (f'{implant.device_name} is MR Conditional — '
                                f'verify specific conditions (field strength, SAR limits) '
                                f'before proceeding. Verification: {implant.verification_status}.'),
                })
            elif implant.mr_safety_class == 'Unknown':
                category = (implant.device_category or '').lower()
                if any(k.lower() in category for k in MR_UNSAFE_CATEGORIES):
                    # An active device of a category that is unsafe by default
                    # must block until proven otherwise.
                    has_unsafe = True
                    warnings.append({
                        'severity': 'Critical',
                        'category': 'implant',
                        'title': f'Unverified {implant.device_category}: {implant.device_name}',
                        'message': (f'{implant.device_name} is a {implant.device_category} with no '
                                    f'verified MR safety classification. Treat as MR Unsafe until '
                                    f'the manufacturer conditions are verified.'),
                    })
                else:
                    has_unknown = True
                    warnings.append({
                        'severity': 'Review Required',
                        'category': 'implant',
                        'title': f'Unknown MRI Safety Status: {implant.device_name}',
                        'message': (f'{implant.device_name} — MRI safety classification is unknown. '
                                    f'Additional verification required.'),
                    })

        # Check for known unsafe categories without specific device records
        if not implants:
            warnings.append({
                'severity': 'Informational',
                'category': 'screening',
                'title': 'No Implant Registry Entries',
                'message': 'No implant/device records found. Ensure screening questionnaire is completed.',
            })

        # Determine overall status
        if has_unsafe:
            status = 'Safety Concern'
        elif has_unknown or has_conditional:
            status = 'Additional Verification Required'
        elif profile.mri_screening_status == 'Cleared' and not implants:
            status = 'Cleared'
        else:
            status = profile.mri_screening_status

        # Gadolinium-specific
        order = None
        if order_id:
            order = db.session.get(RadiologyOrder, order_id)
        is_gadolinium = False
        if order and order.notes:
            is_gadolinium = 'gadolinium' in (order.notes or '').lower() or 'contrast' in (order.notes or '').lower()

        if is_gadolinium:
            if profile.last_egfr is not None and profile.last_egfr < 30:
                warnings.append({
                    'severity': 'Critical',
                    'category': 'renal',
                    'title': 'Gadolinium + Severe Renal Impairment',
                    'message': (f'eGFR: {profile.last_egfr} — '
                                f'Gadolinium contrast in severe renal impairment '
                                f'carries risk of NSF. Review necessity.'),
                })
            if profile.previous_contrast_reaction and profile.previous_contrast_type == 'Gadolinium':
                warnings.append({
                    'severity': 'Important',
                    'category': 'contrast',
                    'title': 'Previous Gadolinium Reaction',
                    'message': 'Patient has a documented previous reaction to gadolinium-based contrast.',
                })

        return {
            'safety_status': status,
            'warnings': warnings,
            'implants': [{
                'name': i.device_name,
                'category': i.device_category,
                'manufacturer': i.manufacturer,
                'model': i.model_number,
                'safety_class': i.mr_safety_class,
                'verification': i.verification_status,
            } for i in implants],
            'screening_status': profile.mri_screening_status,
            'is_gadolinium': is_gadolinium,
        }

    def evaluate_contrast_safety(self, patient_id, contrast_type='Iodinated'):
        """Evaluate contrast-specific safety for a patient."""
        profile = self.get_or_create_safety_profile(patient_id)
        warnings = []

        if profile.previous_contrast_reaction:
            prev_type = (profile.previous_contrast_type or '').strip()
            same_agent = contrast_type.lower() in prev_type.lower() if prev_type else False
            warnings.append({
                'severity': 'Critical' if same_agent else 'Important',
                'category': 'contrast',
                'title': (f'Previous {contrast_type} Contrast Reaction' if same_agent
                          else 'Previous Contrast Reaction (agent '
                               + (prev_type or 'not recorded') + ')'),
                'message': (f'Patient has a documented contrast reaction: '
                            f'{profile.contrast_reaction_details or "Details not recorded"}. '
                            f'Consider pre-medication or alternative contrast and confirm the agent involved.'),
            })

        if profile.last_egfr is not None:
            threshold = 30 if contrast_type == 'Gadolinium' else 45
            if profile.last_egfr < threshold:
                warnings.append({
                    'severity': 'Critical' if profile.last_egfr < 30 else 'Important',
                    'category': 'renal',
                    'title': f'Renal Function — {contrast_type} Contrast',
                    'message': (f'eGFR: {profile.last_egfr} mL/min. '
                                f'{"Contraindicated" if profile.last_egfr < 30 else "Use with caution"} '
                                f'with {contrast_type} contrast.'),
                })

        # Check for previous contrast administrations
        prev_admin = ContrastAdministration.query.filter_by(
            patient_id=patient_id).filter(
            ContrastAdministration.contrast_type == contrast_type
        ).order_by(ContrastAdministration.administration_time.desc()).first()

        if prev_admin and prev_admin.reaction_occurred:
            warnings.append({
                'severity': 'Important',
                'category': 'contrast',
                'title': f'Previous Reaction During {contrast_type} Administration',
                'message': (f'Reaction during {prev_admin.contrast_agent}: '
                            f'{prev_admin.reaction_severity} — {prev_admin.reaction_description or ""}'),
            })

        return {
            'warnings': warnings,
            'previous_reactions': ContrastAdministration.query.filter_by(
                patient_id=patient_id, reaction_occurred=True
            ).count(),
        }

    def get_required_screening(self, modality):
        """Return the required screening checklist for a modality."""
        screening = {
            'CT': {
                'with_contrast': [
                    'Previous contrast reaction', 'Allergy status',
                    'Renal function (creatinine/eGFR)', 'Pregnancy status',
                    'Current medications', 'Diabetes medications (metformin)',
                ],
                'without_contrast': [
                    'Pregnancy status (if applicable)',
                ],
            },
            'MRI': {
                'standard': [
                    'Pacemaker/ICD screening', 'Implanted device screening',
                    'Metallic foreign body screening', 'Claustrophobia',
                    'Sedation requirements', 'Ability to lie still',
                    'Communication limitations', 'Pregnancy status',
                ],
                'with_gadolinium': [
                    'All standard MRI screening',
                    'Renal function (creatinine/eGFR)',
                    'Previous gadolinium reaction',
                    'Pregnancy status',
                ],
            },
            'X-ray': {
                'standard': ['Pregnancy status (if applicable)'],
            },
            'Nuclear Medicine': {
                'standard': [
                    'Pregnancy status', 'Breastfeeding status',
                    'Current medications', 'Recent nuclear studies',
                ],
            },
        }
        return screening.get(modality, {})

    def get_patient_safety_alerts(self, patient_id):
        """Get all active safety alerts for a patient across all modalities."""
        alerts = []
        alerts.extend(self.generate_dose_alerts(patient_id))
        ct_eval = self.evaluate_ct_safety(patient_id)
        alerts.extend(ct_eval.get('warnings', []))
        mri_eval = self.evaluate_mri_safety(patient_id)
        alerts.extend(mri_eval.get('warnings', []))
        return alerts

    def generate_dose_alerts(self, patient_id):
        """Delegate to dose service for radiation alerts."""
        from app.services.radiology.dose_service import RadiationDoseService
        return RadiationDoseService().generate_dose_alerts(patient_id)

    def create_safety_screening(self, order_id, patient_id, screening_type,
                                **kwargs):
        """Create a safety screening record for an imaging order."""
        screening = ImagingSafetyScreening(
            order_id=order_id,
            patient_id=patient_id,
            screening_type=screening_type,
            **{k: v for k, v in kwargs.items() if v is not None})
        db.session.add(screening)
        db.session.commit()
        return screening

    def complete_safety_screening(self, screening_id, user_id, **kwargs):
        """Mark a safety screening as completed."""
        screening = db.session.get(ImagingSafetyScreening, screening_id)
        if not screening:
            return None
        for k, v in kwargs.items():
            if hasattr(screening, k):
                setattr(screening, k, v)
        screening.screening_completed_by = user_id
        screening.screening_completed_at = datetime.now()
        screening.screening_status = 'Cleared'
        db.session.commit()
        return screening
