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
        | `W` | Accept |
        | `X` | Reject |
        | `K` | Move to `_keep` |
        | `M` | Move to a folder |
        | `Delete` | Trash |
        | `0`–`5` | Rate |
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
        | `D`, then `1`–`5` | Dispute the AI decision |

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

        **Adapter** — a small preference model trained from your own labels in a folder. It personalizes the AI ranking. See [What adapters are](doc:what-adapters-are).

        **CLIP / TOPIQ** — the base AI models that score images for content and technical quality during [Index & Score](doc:index-score).

        **DINO** — an optional prefilter that flags likely rejects before full scoring. See [Prefilters: DINO & pHash](doc:prefilters).

        **Dispute** — a weighted correction saved when the AI clearly gets an image wrong. See [Disputing AI decisions](doc:disputing).

        **pHash (perceptual hash)** — a fingerprint of an image's appearance, used to detect near-duplicates.

        **Pool Removal** — a prefilter mode that excludes flagged images from the scoring pool entirely.

        **Score Fit** — a score-regression metric showing how closely an adapter matched your labels in testing. See [Evaluating an adapter](doc:evaluating).

        **Soft Quarantine** — a prefilter mode that flags questionable images without hiding or removing them.

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

        The AI runtime and downloaded model files are installed once for the app, not per folder. Manage them from **`AI > Runtime And Cache > Install AI Runtime...`** and **`AI > Runtime And Cache > Download AI Models...`**.
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

        The AI runtime or models may not be installed. Open **`AI > Runtime And Cache > Install AI Runtime...`** or **`Download AI Models...`** and check the setup state. See [Where AI files live](doc:where-files-live).

        ## The ranking looks stale

        Re-rank with **`AI > Adapter Training > Rank Folder With Local Adapter`**. If the folder changed a lot (images added or removed), rerun [Index & Score](doc:index-score) first.

        ## I only want to review AI results, not train

        You do not need the adapter steps at all. Run [Index & Score](doc:index-score), then review in [AI Review](doc:ai-review). Adapters are optional.

        ## My adapter does not match my taste

        It may need more — or more balanced — labels. Make sure you are labeling clear keeps *and* rejects, cover the uncertain middle with [guided review](doc:review-labels), and [evaluate](doc:evaluating) after each batch.

        ## Did I lose my originals?

        No. Image Triage only moves, copies, or deletes when you ask it to, and `Ctrl+Z` reverses the last change. Collections and favorites never touch the files themselves.

        ## A collection shows missing items

        The underlying files were moved or renamed outside Image Triage. Restore their paths or re-add them — see [Virtual collections](doc:collections).
        """,
    ),
]
