from __future__ import annotations

from .help_dialog import HelpPage


def ai_workflow_center_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "Overview",
            """
            # AI Workflow Center

            The AI Workflow Center is the control panel for this folder's AI sorting. It shows what has run, what to do next, and which current models power the result.

            Use it to see:

            - Which AI steps are available
            - Which steps have already run
            - What each step does, and the next useful action

            ## Layout

            - **Left** — the main workflow steps, in order.
            - **Center** — an explanation of the step you select.
            """,
        ),
        HelpPage(
            "Workflow Steps",
            """
            # Workflow steps

            The AI workflow runs in this order:

            1. **Setup** — choose CPU or GPU and install the runtime with CLIP, TOPIQ, and InsightFace.
            2. **Cull & Score** — group duplicates, analyze quality and content, cluster similar work, and rank the folder.
            3. **Review Results** — inspect the AI Pick, Keeper, Needs Review, and Reject buckets.
            4. **Apply Decisions** — organize the clearest picks, rejects, or semantic categories.
            """,
        ),
        HelpPage(
            "Prefilter Decisions",
            """
            # Prefilter decisions

            Prefilters are optional early checks that reduce how many images reach the full AI scoring stage.

            ## pHash Prefilter

            Detects very similar images, such as tight near-duplicates.

            ## Pool Removal

            Prefilters keep flagged images out of the main CLIP/TOPIQ scoring stage. This saves processing time without hiding or deleting those images. They remain available for manual review, and any image already marked as a winner is protected from removal.
            """,
        ),
        HelpPage(
            "How Scoring Works",
            """
            # How scoring works

            The current cull uses one base-model pipeline. It does not need a training or adapter step.

            - **pHash** groups near-duplicate frames.
            - **CLIP** scores visual relevance and composition and assigns semantic categories.
            - **TOPIQ** contributes technical image-quality signals.
            - **InsightFace** contributes face and eye quality when faces are present.
            - Category clustering and diversity penalties prevent a burst of similar frames from dominating the top results.

            Keep and review percentages divide the finished ranking into actionable buckets; they do not retrain the models.
            """,
        ),
        HelpPage(
            "Result Filters",
            """
            # Filters for checking AI results

            These filters let you inspect what the AI did:

            - **AI Ingested** — images that reached the main CLIP/TOPIQ scoring step.
            - **AI Prefilter Dumped** — images removed from AI scoring by the duplicate prefilter.
            - **AI Top Picks** — the strongest current AI keep candidates.

            Use these views to confirm the prefilters are helping. Removed images remain visible and can still be kept during manual review.
            """,
        ),
    )


def workflow_builder_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "Workflow Recipes",
            """
            # Workflow recipes

            A workflow recipe is a saved export or handoff setup. Recipes let you repeat the same output process without rebuilding the settings each time.

            They are useful whenever you copy, move, resize, convert, export, or archive images the same way — for example:

            - Client delivery folders
            - Editor handoff folders
            - Proofing sets
            - Social media exports
            - Final archives
            - Backup copies
            - Selected image bundles
            """,
        ),
        HelpPage(
            "Content Mode",
            """
            # Content mode

            Content mode controls what kind of output the recipe creates.

            ## Export Deliverables

            Creates new output files using your chosen resize, conversion, metadata, and filename settings.

            Use it for polished final images, web-ready files, client previews, or edited deliverables.

            ## Full Bundle

            Works with the original selected image bundles.

            Use it when another person, program, or editing workflow needs the originals. Depending on the transfer settings, this can keep RAW files, sidecars, and related files together.

            **In short:** choose Export Deliverables for finished output files, and Full Bundle to hand off or preserve the originals.
            """,
        ),
        HelpPage(
            "Transfer Mode",
            """
            # Transfer mode

            Transfer mode controls what happens to the selected files or bundles.

            ## Copy

            Leaves the originals in place and creates a copy elsewhere.

            ## Move

            Relocates the selected files to a new location, changing where the originals are stored.

            ## Archive

            Packages the selected files or bundles into an archive file.

            **Tip:** when unsure, start with Copy. It is the safest option because it never moves or removes your originals. The preview panel shows what the recipe will do before you run it.
            """,
        ),
        HelpPage(
            "Saved Recipes",
            """
            # Saved recipes

            Save a recipe whenever you expect to reuse the same setup.

            - **Built-in recipes** are ready-made starting points.
            - **Saved recipes** are your own custom presets.

            Even after selecting a saved recipe, you can still edit the fields before running it.
            """,
        ),
    )


def library_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "Library Panel",
            """
            # The Library panel

            The Library panel helps you navigate, organize, and quickly return to image folders.

            It includes:

            - Favorites
            - Folder browsing
            - Virtual collections
            - Catalog tools

            The Library does not replace normal folder browsing. It simply makes it easier to reopen important folders and build image sets across different locations.
            """,
        ),
        HelpPage(
            "Favorites",
            """
            # Favorites

            Favorites are shortcuts to folders you use often. They do not move, copy, or change your files — they just make frequently used folders easier to find and reopen.

            Use favorites for folders such as:

            - Current projects
            - Client folders
            - Import folders
            - Export folders
            - Common editing locations
            """,
        ),
        HelpPage(
            "Virtual Collections",
            """
            # Virtual collections

            Virtual collections are saved groups of image references. They let you gather images together without moving or copying the original files.

            Use collections for sets such as:

            - Portfolio candidates
            - Client proofing sets
            - Images to edit later
            - Trip selects
            - Cross-folder themes
            - Final candidates
            - Review queues

            Removing an image from a collection does not delete it from your computer, and deleting a collection does not delete the original files.
            """,
        ),
        HelpPage(
            "Global Catalog",
            """
            # Global catalog

            The global catalog is an optional, searchable index of folders you choose. It lets you search filenames, paths, and cached image bundle information without opening each folder by hand.

            It is useful for:

            - Finding older work
            - Rebuilding collections
            - Opening images from multiple folders
            - Quickly checking cached folder contents

            AI caches are still stored per folder. The catalog helps with search and navigation — it does not change how folder-local AI processing works.
            """,
        ),
    )


def catalog_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "What It Is",
            """
            # The catalog

            The catalog is an optional index of folders you choose. It stores enough information to help you search and reopen image bundles quickly, without rescanning every folder each time.

            The catalog does not move your files or force a new workflow. It is simply a faster way to find and reopen existing work.
            """,
        ),
        HelpPage(
            "How To Use It",
            """
            # How to use the catalog

            1. Add one or more root folders from the **Library** menu.
            2. Refresh the catalog index.
            3. Search or browse the catalog by filename or path.
            4. Open results as a virtual catalog view.

            Catalog views are for discovery and navigation. They let you inspect found images without changing where the originals are stored.
            """,
        ),
        HelpPage(
            "When To Use It",
            """
            # When the catalog helps

            The catalog is most useful when you want to:

            - Find images across multiple folders
            - Search old shoots
            - Build a collection from past work
            - Reopen a known project without browsing to it manually
            - Quickly inspect cached folder information

            For active editing inside a single folder, normal folder browsing is usually the better choice.
            """,
        ),
    )


def collection_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "What Collections Are",
            """
            # Collections

            A collection is a named group of image bundle references. Collections do not copy, move, or delete files — they remember where selected images live so you can reopen that working set later.

            Think of a collection as a playlist for images: it points to files, but it does not contain them.
            """,
        ),
        HelpPage(
            "Good Uses",
            """
            # Good uses for collections

            Collections are useful for sets such as:

            - Portfolio candidates
            - Client proofing groups
            - Edit queues
            - Images to revisit later
            - Cross-folder themes
            - Final selects
            - Social media candidates
            - Comparison sets

            Choose a name that matches how you plan to use the set later — for example, "Smith Wedding Proofs," "Portfolio Maybes," or "Colorado Trip Selects."
            """,
        ),
        HelpPage(
            "Limits",
            """
            # Collection limits

            Collections depend on the original files staying in the same location.

            If images are moved, renamed, or deleted outside Image Triage, the collection may show those items as missing.

            To fix missing items, move the files back, update their paths, or add the images to the collection again.
            """,
        ),
    )


def settings_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "Settings Areas",
            """
            # What settings control

            Settings are grouped by area so you can adjust one part of the app without hunting through a single long list.

            ## General and Interface

            Overall app behavior, layout, and display preferences.

            ## Library and Folders

            Folder loading, catalog cache behavior, and how the app handles image bundle data.

            ## AI

            The main CLIP/TOPIQ/InsightFace scoring workflow, keeper thresholds, and review bands.

            ## pHash Prefilter

            Duplicate and near-duplicate detection using perceptual hashing.
            """,
        ),
        HelpPage(
            "AI Settings",
            """
            # AI settings

            Core AI settings affect how images are scored, ranked, grouped, and reviewed.

            pHash settings affect which near-duplicates reach the main scoring stage. Because the prefilter runs before the main AI ranking, change it conservatively.

            After changing prefilter settings, check the results with these filters:

            - AI Ingested
            - AI Prefilter Dumped
            - AI Top Picks

            These views confirm whether the AI is filtering the right images before you rely on it for a full triage pass.
            """,
        ),
    )
