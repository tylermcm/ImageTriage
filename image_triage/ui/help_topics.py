from __future__ import annotations

from .help_dialog import HelpPage


def ai_workflow_center_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "Overview",
            """
            # AI Workflow Center

            The AI Workflow Center is the control panel for this folder's AI sorting. It shows what has run, what to do next, and how your trained models performed.

            Use it to see:

            - Which AI steps are available
            - Which steps have already run
            - What each step does, and the next useful action
            - Which trained preference models exist, and how well they scored

            ## Layout

            - **Left** — the main workflow steps, in order.
            - **Center** — an explanation of the step you select.
            - **Right** — trained adapters and their Score Fit results.
            """,
        ),
        HelpPage(
            "Workflow Steps",
            """
            # Workflow steps

            The AI workflow runs in this order:

            1. **Setup** — install the AI runtime and model files.
            2. **DINO Prefilter** — flag likely rejects before scoring (optional).
            3. **Index & Score** — run the main CLIP/TOPIQ scoring pass.
            4. **Review Labels** — confirm or correct example images.
            5. **Train Adapter** — build a preference model from your labels.
            6. **Evaluate** — measure how well the adapter performs.
            7. **Rank & Apply** — apply the final ranking to the folder.

            When DINO or pHash prefiltering is enabled, it runs before the main CLIP/TOPIQ scoring step.

            Prefilters only remove or flag obvious problem images early. The main AI culler still makes the final ranking decisions.
            """,
        ),
        HelpPage(
            "Prefilter Decisions",
            """
            # Prefilter decisions

            Prefilters are optional early checks that reduce how many images reach the full AI scoring stage.

            ## DINO Prefilter

            Examines each image and flags those that are likely bad, unwanted, or not worth scoring further.

            ## pHash Prefilter

            Detects very similar images, such as tight near-duplicates. It works independently of DINO.

            ## Soft Quarantine

            Marks images as questionable without hiding or deleting them. They stay visible but labeled, so you can inspect them later.

            ## Pool Removal

            Keeps flagged images out of the main CLIP/TOPIQ scoring stage. This is faster, but those images are then excluded from the main AI ranking.

            **Tip:** when testing new thresholds, start with Soft Quarantine. It lets you see what the AI would have removed before anything is actually excluded from later steps.
            """,
        ),
        HelpPage(
            "Adapters",
            """
            # Adapters

            An adapter is a small preference model trained from the labels you save in a folder. It teaches the AI what you consider good or bad for that specific set of images.

            ## Score Fit

            Score Fit shows how closely an adapter's scores matched your saved labels during testing. It is a score-regression fit metric, not a full measure of culling accuracy.

            ## Before you train

            - Label enough clear examples across at least two label types, such as keep and reject.
            - Train the adapter only once you have that spread of examples.
            - Run an evaluation before relying on a new adapter.
            """,
        ),
        HelpPage(
            "Disputing AI Decisions",
            """
            # Disputing AI decisions

            A dispute is a correction you save when the AI clearly gets a specific image wrong — for example, marking a keeper as a reject, or promoting an image you would reject.

            ## How to dispute

            With an image selected in **AI Review**, do any of the following:

            - Click **Dispute AI** in the AI Review toolbar.
            - Right-click the image and choose **Dispute AI Decision**.
            - Press `D`, then a rating key from `1` to `5`.

            The rating keys mean:

            1. Best
            2. Strong
            3. Maybe
            4. Weak
            5. Reject

            ## Why disputes matter

            Disputes are saved as adapter training labels with extra weight, so they teach the adapter more strongly than a normal label.

            Use disputes for meaningful corrections, not every small preference. A focused set of clear disputes helps the adapter learn quickly, while noisy disputes can make the next adapter less stable.
            """,
        ),
        HelpPage(
            "Result Filters",
            """
            # Filters for checking AI results

            These filters let you inspect what the AI did:

            - **AI Ingested** — images that reached the main CLIP/TOPIQ scoring step.
            - **AI Prefilter Dumped** — images quarantined or removed by DINO or pHash.
            - **DINO Quarantine** — images DINO marked for soft quarantine.
            - **DINO Removed** — images DINO removed from the scoring pool.
            - **AI Top Picks** — the strongest current AI keep candidates.

            Use these views to confirm the prefilters are helping, not accidentally hiding useful images.
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

            The main AI scoring system, including CLIP/TOPIQ scoring, adapter blending, and review bands.

            ## DINO Prefilter

            The optional DINO first pass that detects likely rejects before full scoring.

            ## pHash Prefilter

            Duplicate and near-duplicate detection using perceptual hashing.
            """,
        ),
        HelpPage(
            "AI Settings",
            """
            # AI settings

            Core AI settings affect how images are scored, ranked, grouped, and reviewed.

            DINO and pHash settings affect which images reach the main scoring stage. Because they run before the main AI ranking, changing them too aggressively can hide useful images from later steps — so start conservative.

            After changing prefilter settings, check the results with these filters:

            - AI Ingested
            - AI Prefilter Dumped
            - DINO Quarantine
            - DINO Removed
            - AI Top Picks

            These views confirm whether the AI is filtering the right images before you rely on it for a full triage pass.
            """,
        ),
    )
