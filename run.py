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

app = create_app(os.environ.get('FLASK_CONFIG') or 'development')


@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'Role': Role}


if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')