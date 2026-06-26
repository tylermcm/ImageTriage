from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="settings",
    title="Settings",
    order=6,
    icon="⚙",
    summary="Tune the interface, library behavior, and the AI pipeline.",
)

ARTICLES = [
    DocArticle(
        id="settings-overview",
        title="Settings overview",
        category="settings",
        summary="How settings are grouped by area.",
        keywords=("settings", "preferences", "options", "configure"),
        markdown="""
        # Settings overview

        Settings are grouped by area so you can adjust one part of the app without hunting through a single long list.

        - **General and Interface** — overall behavior, layout, and display preferences.
        - **Library and Folders** — folder loading, catalog cache behavior, and image bundle handling.
        - **AI** — the main scoring system, including CLIP/TOPIQ scoring, adapter blending, and review bands. See [AI settings](doc:ai-settings).
        - **DINO Prefilter** — the optional first pass that detects likely rejects before full scoring.
        - **pHash Prefilter** — duplicate and near-duplicate detection using perceptual hashing.

        > **Tip:** The Settings window has its own **`?`** button for the growing AI, DINO, and pHash sections.
        """,
    ),
    DocArticle(
        id="ai-settings",
        title="AI settings",
        category="settings",
        summary="Tune scoring, adapter blending, and the prefilters safely.",
        keywords=("ai settings", "blend", "prefilter", "dino", "phash", "threshold"),
        markdown="""
        # AI settings

        Core AI settings affect how images are scored, ranked, grouped, and reviewed.

        ## Prefilters run first — change them carefully

        DINO and pHash settings decide which images even reach the main scoring stage. Because they run *before* the AI ranking, changing them too aggressively can hide useful images from later steps. **Start conservative.** See [Prefilters: DINO & pHash](doc:prefilters).

        ## Check your changes

        After changing prefilter settings, audit the results with these filters:

        - AI Ingested
        - AI Prefilter Dumped
        - DINO Quarantine
        - DINO Removed
        - AI Top Picks

        These views confirm the AI is filtering the right images before you rely on it for a full pass.

        ## Adapter blending

        Adapter blending controls how strongly your trained [adapter](doc:what-adapters-are) influences the final score versus the base CLIP/TOPIQ models. Heavier blending makes the ranking more personal; lighter blending keeps it closer to general quality.
        """,
    ),
]
