"""AI integration layer package."""
from .ai_interfaces import (
    AIClinicalAssistant,
    AIPrescriptionChecker,
    AIDrugInteractionEngine,
    AIRadiologyAssistant,
    AIAppointmentOptimization,
    AIHospitalAnalytics,
    AIRehabilitationAssistant,
)
from .clinical_pharmacist import AIClinicalPharmacist
from .ai_diagnosis import AIDiagnosisSupport
from .ai_lab_interpretation import AILaboratoryInterpretation
from .ai_risk_prediction import AIPatientRiskPrediction
from .ai_medical_coding import AIMedicalCodingAssistant
from .ai_clinical_notes import AIClinicalNotes
from .ai_smart_orders import AISmartOrders
from .ai_patient_communication import AIPatientCommunication
from .gemini_base import gemini_available


def get_assistant(name):
    """Factory returning an AI assistant instance by name."""
    registry = {
        'clinical': AIClinicalAssistant,
        'diagnosis': AIDiagnosisSupport,
        'prescription': AIPrescriptionChecker,
        'drug_interaction': AIDrugInteractionEngine,
        'laboratory': AILaboratoryInterpretation,
        'radiology': AIRadiologyAssistant,
        'risk': AIPatientRiskPrediction,
        'appointment': AIAppointmentOptimization,
        'coding': AIMedicalCodingAssistant,
        'analytics': AIHospitalAnalytics,
        'rehabilitation': AIRehabilitationAssistant,
        'clinical_notes': AIClinicalNotes,
        'smart_orders': AISmartOrders,
        'patient_communication': AIPatientCommunication,
    }
    cls = registry.get(name)
    return cls() if cls else None
