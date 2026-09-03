from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="reference",
    title="Reference",
    order=7,
    icon="\U0001F4D6",
    summary="Shortcuts, glossary, file locations, and troubleshooting.",
)

ARTICLES = [
    DocArticle(
        id="shortcuts",
        title="Keyboard shortcuts",
        category="reference",
        summary="The complete keyboard reference.",
        keywords=("shortcut", "shortcuts", "keyboard", "hotkey", "keys"),
        markdown="""
        # Keyboard shortcuts

        ## Sorting

        | Key | Action |
        | --- | --- |
        | `W` | Mark winner |
        | `X` | Reject |
        | `K` | Move to `_keep` |
        | `M` | Move to a folder |
        | `Delete` | Trash |
        | `T` | Tag |
        | `Ctrl+Z` | Undo last change |

        ## Selection

        | Key | Action |
        | --- | --- |
        | `Ctrl`-click | Add / remove from selection |
        | `Shift`-click | Select a range |
        | `Ctrl+A` | Select all visible |
        | `Ctrl+Shift+X` | Clear filters |

        ## Preview & compare

        | Key | Action |
        | --- | --- |
        | `Space` / `Enter` | Open Preview |
        | `Z` | Zoom |
        | `0` | Fit |
        | `L` | Toggle loupe |
        | `C` | Toggle compare |
        | `Tab` | Change preview focus |
        | `[` / `]` | Cycle a burst in the viewer |

        ## AI review

        | Key | Action |
        | --- | --- |
        | `Ctrl+Alt+P` | Next AI top pick |
        | `Ctrl+Alt+G` | Compare current AI group |

        See [Working keyboard-first](doc:keyboard-first) for how to put these together.
        """,
    ),
    DocArticle(
        id="glossary",
        title="Glossary",
        category="reference",
        summary="Plain-language definitions of the terms used across the app.",
        keywords=("glossary", "terms", "definitions", "vocabulary", "meaning"),
        markdown="""
        # Glossary

        **CLIP / TOPIQ / InsightFace** — the current models that score image content, technical quality, and face quality during [Cull & Score](doc:index-score).

        **pHash (perceptual hash)** — a fingerprint of an image's appearance, used to detect near-duplicates.

        **Pool Removal** — prefilter behavior that excludes flagged images from AI scoring while leaving them available for manual review.

        **Virtual collection** — a saved set of image references that does not move or copy files. See [Virtual collections](doc:collections).
        """,
    ),
    DocArticle(
        id="where-files-live",
        title="Where AI files live",
        category="reference",
        summary="The hidden per-folder workspace the AI uses.",
        keywords=("files", "workspace", "cache", "artifacts", "report", "hidden"),
        markdown="""
        # Where AI files live

        Every AI-enabled folder gets a hidden workspace beside the images:

        - **`.image_triage_ai/artifacts`** — the AI database and intermediate artifacts.
        - **`.image_triage_ai/ranker_report`** — scored exports and the HTML report.

        ## Why per-folder

        Keeping the AI cache next to the images means a folder is self-contained: copy it elsewhere and its scores travel with it. It also means catalog search and collections never change how a folder's AI processing works — see [The global catalog](doc:catalog).

        ## Runtime and models

        The AI runtime and culling models are installed together once for the app, not per folder. Manage them from **`AI > AI Setup And Cache > Set Up AI...`**.
        """,
    ),
    DocArticle(
        id="faq",
        title="FAQ & troubleshooting",
        category="reference",
        summary="Common questions and quick fixes.",
        keywords=("faq", "troubleshooting", "help", "problem", "fix", "disabled"),
        markdown="""
        # FAQ & troubleshooting

        ## AI actions are greyed out

        The AI runtime or culling models may not be installed. Open **`AI > AI Setup And Cache > Set Up AI...`** and check the setup state. See [Where AI files live](doc:where-files-live).

        ## The ranking looks stale

        Use **Quick Rerank** if the folder is unchanged. If images were added or removed, rerun [Cull & Score](doc:index-score).

        ## I only want to review AI results

        Run [Cull & Score](doc:index-score), then inspect the base-model ranking in [AI Review](doc:ai-review). No training step is required.

        ## Did I lose my originals?

        No. Image Triage only moves, copies, or deletes when you ask it to, and `Ctrl+Z` reverses the last change. Collections and favorites never touch the files themselves.

        ## A collection shows missing items

        The underlying files were moved or renamed outside Image Triage. Restore their paths or re-add them — see [Virtual collections](doc:collections).
        """,
    ),
]
