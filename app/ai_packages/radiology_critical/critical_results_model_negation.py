import pandas as pd
import numpy as np
import re
from datetime import datetime
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import classification_report, confusion_matrix, roc_auc_score
import joblib
import os

class NegationHandlingDetector:
    """
    Enhanced detector with proper negation handling.
    """
    
    def __init__(self):
        self.vectorizer = None
        self.model = None
        self.is_trained = False
        
        # Negation patterns to check
        self.negation_patterns = [
            r'no\s+(\w+\s+){0,3}evidence\s+of',
            r'no\s+(\w+\s+){0,3}findings?\s+of',
            r'no\s+(\w+\s+){0,3}signs?\s+of',
            r'without\s+(\w+\s+){0,3}evidence',
            r'absent\s+(\w+\s+){0,3}findings?',
            r'negative\s+for',
            r'excludes?\s+',
            r'rules?\s+out',
            r'no\s+(\w+\s+){0,3}(hemorrhage|pneumothorax|embolism|fracture|mass|tumor)',
        ]
        
        self.critical_keywords = self._build_keyword_dict()
    
    def _build_keyword_dict(self):
        """Build the critical keywords dictionary with better organization"""
        return {
            "Intracranial hemorrhage": {
                "keywords": ["intracranial hemorrhage", "intraparenchymal hemorrhage", 
                            "subarachnoid hemorrhage", "subdural hemorrhage", "epidural hemorrhage",
                            "intraventricular hemorrhage", "brain bleed", "cerebral hemorrhage",
                            "hemorrhagic stroke", "ich"],
                "priority": "CRITICAL"
            },
            "Pneumothorax": {
                "keywords": ["pneumothorax", "tension pneumothorax", "collapsed lung",
                            "air in pleural space", "lung collapse", "pneumothoraces"],
                "priority": "CRITICAL"
            },
            "Pulmonary embolism": {
                "keywords": ["pulmonary embolism", "pe", "saddle embolus", "acute pulmonary embolus",
                            "thromboembolism", "pulmonary emboli"],
                "priority": "CRITICAL"
            },
            "Aortic dissection": {
                "keywords": ["aortic dissection", "aortic aneurysm", "aortic rupture",
                            "intramural hematoma", "penetrating aortic ulcer", "aaa"],
                "priority": "CRITICAL"
            },
            "Bowel perforation": {
                "keywords": ["bowel perforation", "intestinal perforation", "perforated viscus",
                            "free air", "pneumoperitoneum", "perforation"],
                "priority": "CRITICAL"
            },
            "Spinal cord compression": {
                "keywords": ["spinal cord compression", "spinal stenosis", "cord impingement",
                            "cauda equina", "cord compression"],
                "priority": "URGENT"
            },
            "Ectopic pregnancy": {
                "keywords": ["ectopic pregnancy", "tubal pregnancy", "adnexal mass"],
                "priority": "CRITICAL"
            },
            "Critical fracture": {
                "keywords": ["unstable fracture", "cervical spine fracture", "pelvic fracture",
                            "skull fracture", "vertebral fracture", "spine fracture",
                            "c-spine fracture", "burst fracture", "compression fracture"],
                "priority": "URGENT"
            },
            "Malignancy": {
                "keywords": ["malignancy", "tumor", "mass", "neoplasm", "carcinoma",
                            "suspicious lesion", "cancer", "metastasis", "metastases"],
                "priority": "URGENT"
            },
            "Acute appendicitis": {
                "keywords": ["acute appendicitis", "appendiceal", "appendicolith",
                            "periappendiceal abscess", "appendicitis"],
                "priority": "URGENT"
            }
        }
    
    def has_negation(self, text):
        """
        Check if the text contains negation patterns.
        
        Returns:
            tuple: (has_negation, negated_terms)
        """
        text_lower = text.lower()
        negated_terms = []
        
        for pattern in self.negation_patterns:
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            if matches:
                # Extract what's being negated
                for match in matches:
                    if isinstance(match, tuple):
                        negated_terms.extend([term for term in match if term])
                    else:
                        negated_terms.append(match)
        
        return len(negated_terms) > 0, negated_terms
    
    def preprocess_text(self, text):
        """Preprocess text for ML"""
        if not text or pd.isna(text):
            return ""
        
        text = str(text).lower()
        text = re.sub(r'[^\w\s\-/]', ' ', text)
        text = ' '.join(text.split())
        return text
    
    def train(self, dataset_path, test_size=0.2, random_state=42):
        """Train the model"""
        print("📊 Loading dataset...")
        self.dataset = pd.read_csv(dataset_path)
        print(f"   Loaded {len(self.dataset)} records")
        
        # Prepare features
        print("🔄 Preparing features...")
        self.dataset['combined_text'] = self.dataset['findings'].fillna('') + ' ' + self.dataset['impression'].fillna('')
        self.dataset['combined_text'] = self.dataset['combined_text'].apply(self.preprocess_text)
        
        # Add negation feature (this helps the model learn negation patterns)
        self.dataset['has_negation'] = self.dataset['combined_text'].apply(
            lambda x: 1 if self.has_negation(x)[0] else 0
        )
        
        # Prepare labels
        X_text = self.dataset['combined_text'].values
        X_neg = self.dataset['has_negation'].values.reshape(-1, 1)
        y = self.dataset['critical_finding'].astype(int).values
        
        # Split data
        X_text_train, X_text_test, X_neg_train, X_neg_test, y_train, y_test = train_test_split(
            X_text, X_neg, y, test_size=test_size, random_state=random_state, stratify=y
        )
        print(f"   Training: {len(X_text_train)} records")
        print(f"   Testing: {len(X_text_test)} records")
        
        # Vectorize text
        print("🔄 Vectorizing text (TF-IDF)...")
        self.vectorizer = TfidfVectorizer(
            max_features=3000,
            ngram_range=(1, 3),
            min_df=2,
            max_df=0.95,
            stop_words='english'
        )
        
        # Combine text features with negation feature
        X_train_text_vectors = self.vectorizer.fit_transform(X_text_train)
        
        # Train model
        print("🚀 Training Logistic Regression model...")
        self.model = LogisticRegression(
            C=1.0,
            class_weight='balanced',
            max_iter=1000,
            random_state=random_state,
            solver='liblinear'
        )
        self.model.fit(X_train_text_vectors, y_train)
        
        # Evaluate
        X_test_text_vectors = self.vectorizer.transform(X_text_test)
        y_pred = self.model.predict(X_test_text_vectors)
        y_pred_proba = self.model.predict_proba(X_test_text_vectors)[:, 1]
        
        # Calculate metrics
        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
        
        accuracy = accuracy_score(y_test, y_pred)
        precision = precision_score(y_test, y_pred)
        recall = recall_score(y_test, y_pred)
        f1 = f1_score(y_test, y_pred)
        auc_roc = roc_auc_score(y_test, y_pred_proba)
        
        self.metrics = {
            'accuracy': accuracy,
            'precision': precision,
            'recall': recall,
            'f1': f1,
            'auc_roc': auc_roc,
            'test_size': len(X_text_test)
        }
        
        print("\n" + "="*60)
        print("📊 ENHANCED MODEL WITH NEGATION HANDLING")
        print("="*60)
        print(f"Accuracy:  {accuracy:.2%}")
        print(f"Precision: {precision:.2%}")
        print(f"Recall:    {recall:.2%}")
        print(f"F1 Score:  {f1:.2%}")
        print(f"AUC-ROC:   {auc_roc:.2%}")
        print("="*60)
        
        cm = confusion_matrix(y_test, y_pred)
        print("\n📊 Confusion Matrix:")
        print(f"   True Negatives:  {cm[0][0]}")
        print(f"   False Positives: {cm[0][1]}")
        print(f"   False Negatives: {cm[1][0]}")
        print(f"   True Positives:  {cm[1][1]}")
        print("="*60)
        
        self.is_trained = True
        return self.metrics
    
    def predict(self, report_text, threshold=0.5):
        """Predict with negation handling"""
        if not report_text or len(str(report_text).strip()) < 10:
            return {
                'critical_finding': False,
                'priority': 'ROUTINE',
                'confidence': 0.0,
                'message': 'Report text too short'
            }
        
        # Clean text
        cleaned_text = self.preprocess_text(report_text)
        
        # Check for negation FIRST (clinical safety)
        has_negation, negated_terms = self.has_negation(cleaned_text)
        
        # Get ML prediction if trained
        ml_confidence = 0.0
        if self.is_trained and self.model and self.vectorizer:
            text_vector = self.vectorizer.transform([cleaned_text])
            proba = self.model.predict_proba(text_vector)[0]
            ml_confidence = proba[1]
        
        # Get keyword matches (with negation awareness)
        keyword_matches = []
        keyword_confidence = 0.0
        
        for category, data in self.critical_keywords.items():
            for keyword in data["keywords"]:
                if keyword.lower() in cleaned_text:
                    # Check if this keyword is negated
                    is_negated = False
                    for negated_term in negated_terms:
                        if keyword.lower() in negated_term.lower():
                            is_negated = True
                            break
                    
                    if not is_negated:
                        keyword_matches.append(keyword)
                        keyword_confidence = min(0.95, keyword_confidence + 0.15)
                    else:
                        # Negated - reduce confidence
                        keyword_confidence = max(0.0, keyword_confidence - 0.10)
        
        # Ensemble prediction
        if self.is_trained:
            # If negation is present, heavily reduce confidence
            if has_negation:
                ml_confidence = ml_confidence * 0.3
            
            ensemble_confidence = (0.7 * ml_confidence) + (0.3 * keyword_confidence)
            ensemble_prediction = ensemble_confidence >= threshold
            
            # Override if negation detected and no positive matches
            if has_negation and not keyword_matches:
                ensemble_prediction = False
                ensemble_confidence = max(0.1, ensemble_confidence * 0.5)
        else:
            ensemble_confidence = keyword_confidence
            ensemble_prediction = keyword_confidence >= threshold
        
        # Determine priority
        if ensemble_prediction and ensemble_confidence >= 0.7:
            priority = 'CRITICAL'
            finding_type = self._get_finding_type(cleaned_text)
            requires_ack = True
            message = 'Critical finding detected with high confidence'
        elif ensemble_prediction and ensemble_confidence >= 0.5:
            priority = 'URGENT'
            finding_type = self._get_finding_type(cleaned_text)
            requires_ack = True
            message = 'Potential critical finding detected, review recommended'
        else:
            priority = 'ROUTINE'
            finding_type = 'Normal/Variant'
            requires_ack = False
            message = 'No critical findings detected'
        
        return {
            'critical_finding': ensemble_prediction,
            'priority': priority,
            'finding_type': finding_type,
            'confidence': round(ensemble_confidence, 3),
            'ml_confidence': round(ml_confidence, 3),
            'keyword_confidence': round(keyword_confidence, 3),
            'requires_acknowledgement': requires_ack,
            'matched_keywords': keyword_matches[:5],
            'has_negation': has_negation,
            'negated_terms': negated_terms[:3] if negated_terms else [],
            'message': message
        }
    
    def _get_finding_type(self, text):
        """Determine finding type"""
        for category, data in self.critical_keywords.items():
            for keyword in data["keywords"]:
                if keyword.lower() in text:
                    return category
        return 'Other critical finding'
    
    def save_model(self, model_dir='models'):
        """Save model"""
        if not self.is_trained:
            print("❌ Model not trained yet")
            return
        
        os.makedirs(model_dir, exist_ok=True)
        model_path = os.path.join(model_dir, 'negation_model.pkl')
        vectorizer_path = os.path.join(model_dir, 'negation_vectorizer.pkl')
        
        joblib.dump(self.model, model_path)
        joblib.dump(self.vectorizer, vectorizer_path)
        print(f"✅ Model saved to {model_path}")

if __name__ == "__main__":
    print("="*60)
    print("🧠 ENHANCED DETECTOR WITH NEGATION HANDLING")
    print("="*60)
    
    detector = NegationHandlingDetector()
    detector.train("data/critical_results_dataset.csv", test_size=0.2)
    
    print("\n🔬 TESTING WITH NEGATION CASES")
    print("="*60)
    
    test_reports = [
        "No evidence of intracranial hemorrhage",
        "No pneumothorax identified",
        "Normal study with no acute findings",
        "Large acute intraparenchymal hemorrhage",
        "No signs of pulmonary embolism",
        "Acute pulmonary embolism with right heart strain",
        "No mass or malignancy detected",
        "The patient has a fracture of C2 with cord compression",
        "No evidence of intracranial hemorrhage or mass effect",
        "Small right pneumothorax with lung collapse"
    ]
    
    for report in test_reports:
        result = detector.predict(report, threshold=0.5)
        print(f"\n📝 Report: {report[:60]}")
        print(f"   Critical: {result['critical_finding']}")
        print(f"   Priority: {result['priority']}")
        print(f"   Confidence: {result['confidence']:.2%}")
        if result.get('has_negation'):
            print(f"   ⚠️ Negation detected: {result['negated_terms']}")
