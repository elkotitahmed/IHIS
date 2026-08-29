"""AI integration layer package."""
from .ai_interfaces import (
    AIClinicalAssistant,
    AIDiagnosisSupport,
    AIPrescriptionChecker,
    AIDrugInteractionEngine,
    AILaboratoryInterpretation,
    AIRadiologyAssistant,
    AIPatientRiskPrediction,
    AIAppointmentOptimization,
    AIMedicalCodingAssistant,
    AIHospitalAnalytics,
    AIRehabilitationAssistant,
)


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
    }
    cls = registry.get(name)
    return cls() if cls else None
