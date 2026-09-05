import os
from app import create_app, db
from app.models import (
    User, Role, Specialty, Doctor, Patient, Department, Appointment,
    MedicalRecord, Diagnosis, Prescription, Medication, LabTestCatalog,
    LabOrder, LabResult, ImagingType, RadiologyOrder, RadiologyReport,
    VitalSign, NursingNote, PharmacyInventory, Notification, Message,
    AuditLog, AIRecommendation, SystemSetting, Dentist, DentalSpecialty,
    DentalRecord, DentalChart, DentalProcedure, DentalImage, OrthodonticCase,
    PhysicalTherapist, TherapyAssessment, TherapySession, TherapyPlan,
    TherapyExercise, ExerciseLibraryItem, RehabilitationProgress,
    FunctionalOutcome, Referral, CareTeam, MultidisciplinaryCase,
)

# run.py is the *development* runner (Flask debug server). The production
# entry point is wsgi.py. A `.env` that pins FLASK_CONFIG=production for the
# deployment host must not turn this debug server into a production process,
# so anything other than an explicit non-production profile falls back to
# development here.
_config_name = os.environ.get('FLASK_CONFIG') or 'development'
if _config_name == 'production':
    print('[run.py] FLASK_CONFIG=production ignored by the development runner; '
          'use wsgi.py (waitress/gunicorn) for production.')
    _config_name = 'development'
app = create_app(_config_name)


@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'Role': Role}


if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)), host='0.0.0.0')