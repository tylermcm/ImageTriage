"""Headless offline image culling engine."""

__all__ = [
    "ActiveQuicksortCuller",
    "AdapterTrainer",
    "CompositeRanker",
    "GlobalRanker",
    "HeadlessFeatureExtractor",
    "IngestionEngine",
    "RankingAwareAdapter",
    "SQLiteFeatureStore",
    "TechnicalTagScorer",
    "ThreadSafeLearningEngine",
    "CLIPTextEncoder",
    "PreferenceLearningScorer",
    "PrimaryCategoryAssigner",
    "ProfileScorer",
    "CategoryClusterer",
    "TextConditionedScorer",
]

_EXPORTS = {
    "ActiveQuicksortCuller": ("aiculler.ranking", "ActiveQuicksortCuller"),
    "AdapterTrainer": ("aiculler.adapter_training", "AdapterTrainer"),
    "CLIPTextEncoder": ("aiculler.text_scoring", "CLIPTextEncoder"),
    "CompositeRanker": ("aiculler.composite_ranking", "CompositeRanker"),
    "GlobalRanker": ("aiculler.ranking", "GlobalRanker"),
    "HeadlessFeatureExtractor": ("aiculler.features", "HeadlessFeatureExtractor"),
    "IngestionEngine": ("aiculler.features", "IngestionEngine"),
    "PreferenceLearningScorer": ("aiculler.preference_learning", "PreferenceLearningScorer"),
    "PrimaryCategoryAssigner": ("aiculler.semantic", "PrimaryCategoryAssigner"),
    "ProfileScorer": ("aiculler.profile_scoring", "ProfileScorer"),
    "RankingAwareAdapter": ("aiculler.adapter", "RankingAwareAdapter"),
    "SQLiteFeatureStore": ("aiculler.storage", "SQLiteFeatureStore"),
    "TechnicalTagScorer": ("aiculler.technical_tags", "TechnicalTagScorer"),
    "TextConditionedScorer": ("aiculler.text_scoring", "TextConditionedScorer"),
    "ThreadSafeLearningEngine": ("aiculler.learning", "ThreadSafeLearningEngine"),
    "CategoryClusterer": ("aiculler.semantic", "CategoryClusterer"),
}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = _EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
