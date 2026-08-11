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
        - **AI** — processing, keeper thresholds, and review bands for the CLIP/TOPIQ/InsightFace cull. See [AI settings](doc:ai-settings).
        - **pHash Prefilter** — duplicate and near-duplicate detection using perceptual hashing.

        > **Tip:** The Settings window has its own **`?`** button for the AI and pHash sections.
        """,
    ),
    DocArticle(
        id="ai-settings",
        title="AI settings",
        category="settings",
        summary="Tune result thresholds and duplicate grouping safely.",
        keywords=("ai settings", "prefilter", "phash", "threshold", "review band"),
        markdown="""
        # AI settings

        Core AI settings affect how images are scored, ranked, grouped, and reviewed.

        ## Prefilters run first — change them carefully

        pHash settings decide which near-duplicates reach the main scoring stage. Because the prefilter runs *before* the AI ranking, change it conservatively. See [pHash prefilter](doc:prefilters).

        ## Check your changes

        After changing prefilter settings, audit the results with these filters:

        - AI Ingested
        - AI Prefilter Dumped
        - AI Top Picks

        These views confirm the AI is filtering the right images before you rely on it for a full pass.

        ## Result thresholds

        **Keep top** controls how much of the ranked folder enters the keeper range. **Review band** holds the next slice for human review instead of treating it as a clear reject. These settings divide the finished ranking; they do not retrain or blend another model.
        """,
    ),
]
