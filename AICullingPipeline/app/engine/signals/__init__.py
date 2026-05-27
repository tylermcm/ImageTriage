"""Modular culling signal stack.

This package is the new foundation for personalized culling:
DINO groups the visual neighborhood, deterministic/specialist layers describe
the image, and the combiner learns how a photographer weighs those signals.
"""

from app.engine.signals.combiner import (
    DEFAULT_PROFILES,
    FEATURE_NAMES,
    ScoringProfile,
    apply_combiner,
    choose_profile,
    feature_values,
    pairwise_feature_delta,
)
from app.engine.signals.dino import DinoSignalLayer, build_dino_signals
from app.engine.signals.evaluation import (
    SIGNAL_EVALUATION_METRICS_FILENAME,
    SIGNAL_EVALUATION_SUMMARY_FILENAME,
    evaluate_culling_signals,
)
from app.engine.signals.models import (
    SIGNAL_SCHEMA_VERSION,
    AestheticSignals,
    DinoSignals,
    FaceSignals,
    FinalDecision,
    ImageSignalRecord,
    LayerStatus,
    PersonalPreferenceSignals,
    SemanticSignals,
    SubjectSignals,
    TechnicalSignals,
)
from app.engine.signals.pipeline import (
    SIGNALS_CSV_FILENAME,
    SIGNALS_FILENAME,
    build_culling_signals,
    save_culling_signals,
)
from app.engine.signals.preference_features import build_preference_feature_rows
from app.engine.signals.specialists import (
    AestheticSpecialistLayer,
    FaceEyeSpecialistLayer,
    ObjectSubjectSpecialistLayer,
    specialist_layers,
)
from app.engine.signals.technical import TechnicalSignalLayer, analyze_technical_quality
from app.engine.signals.training import (
    SIGNAL_COMBINER_FEATURES_FILENAME,
    SIGNAL_COMBINER_WEIGHTS_FILENAME,
    SignalCombinerSourceConfig,
    SignalCombinerTrainingConfig,
    load_learned_weights,
    load_signal_records,
    train_signal_combiner,
)

__all__ = [
    "AestheticSignals",
    "AestheticSpecialistLayer",
    "DEFAULT_PROFILES",
    "DinoSignalLayer",
    "DinoSignals",
    "FEATURE_NAMES",
    "FaceEyeSpecialistLayer",
    "FaceSignals",
    "FinalDecision",
    "ImageSignalRecord",
    "LayerStatus",
    "ObjectSubjectSpecialistLayer",
    "PersonalPreferenceSignals",
    "SIGNALS_CSV_FILENAME",
    "SIGNAL_EVALUATION_METRICS_FILENAME",
    "SIGNAL_EVALUATION_SUMMARY_FILENAME",
    "SIGNAL_COMBINER_FEATURES_FILENAME",
    "SIGNAL_COMBINER_WEIGHTS_FILENAME",
    "SIGNALS_FILENAME",
    "SIGNAL_SCHEMA_VERSION",
    "ScoringProfile",
    "SemanticSignals",
    "SignalCombinerSourceConfig",
    "SignalCombinerTrainingConfig",
    "SubjectSignals",
    "TechnicalSignalLayer",
    "TechnicalSignals",
    "analyze_technical_quality",
    "apply_combiner",
    "build_culling_signals",
    "build_dino_signals",
    "build_preference_feature_rows",
    "choose_profile",
    "evaluate_culling_signals",
    "feature_values",
    "load_learned_weights",
    "load_signal_records",
    "pairwise_feature_delta",
    "save_culling_signals",
    "specialist_layers",
    "train_signal_combiner",
]
