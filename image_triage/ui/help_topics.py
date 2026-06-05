from __future__ import annotations

from .help_dialog import HelpPage


def ai_workflow_center_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "What This Window Does",
            """
            # What this window does

            The AI Workflow Center shows the current status of this folder's AI sorting process.

            Use this window to see:

            - Which AI steps are available
            - Which steps have already been run
            - What each step does
            - What the next useful action is
            - Whether any trained preference models are available
            - How well those models performed

            The workflow list on the left shows the main steps in order.
            The middle area explains the step you selected.
            The right side shows trained adapters and their accuracy results.
            """,
        ),
        HelpPage(
            "Workflow Steps",
            """
            # Workflow steps

            The typical AI workflow is:

            1. Setup
            2. DINO Prefilter
            3. Index and Score
            4. Review Labels
            5. Train Adapter
            6. Evaluate
            7. Rank and Apply

            If DINO or pHash prefiltering is enabled, those run before the main CLIP/TOPIQ scoring step.

            The main AI culler still makes the final ranking decisions. The prefilters are just used to remove or flag obvious problem images earlier in the process.
            """,
        ),
        HelpPage(
            "Prefilter Decisions",
            """
            # Prefilter decisions

            Prefilters are optional early checks that help reduce the number of images sent into the full AI scoring system.

            ## DINO Prefilter

            DINO looks at the image itself and tries to identify images that are likely to be bad, unwanted, or not worth scoring further.

            ## pHash Prefilter

            pHash looks for very similar-looking images, such as tight near-duplicates. It works separately from DINO.

            ## Soft Quarantine

            Soft quarantine marks images as questionable without hiding or deleting them. The images stay visible, but they are labeled so you can inspect them later.

            ## Pool Removal

            Pool removal keeps dumped images out of the main CLIP/TOPIQ scoring stage. This can speed things up, but it also means those images will not be considered by the main AI ranking system.

            When testing new thresholds, use Soft Quarantine first. It is safer because you can see what the AI would have removed before actually excluding anything from later steps.
            """,
        ),
        HelpPage(
            "Adapters",
            """
            # Adapters

            An adapter is a small preference model trained from your own saved labels in this folder.

            It helps the AI learn what you consider good or bad for this specific set of images.

            Adapter accuracy shows how often the adapter matched your labels during testing. Higher accuracy means it agreed with your choices more often.

            Train an adapter only after you have labeled enough clear examples across at least two different label types, such as keep and reject.

            Before relying on a new adapter, run an evaluation to see how well it performs.
            """,
        ),
        HelpPage(
            "Disputing AI Decisions",
            """
            # Disputing AI decisions

            A dispute is a correction you save when the AI makes a bad call on a specific image.

            Use it when the AI score or bucket is clearly wrong. For example, dispute an image if the AI marks a keeper as a reject, or if it promotes an image you know should be rejected.

            To dispute the selected image in AI Review:

            - Click Dispute AI in the AI Review toolbar.
            - Or right-click the image and choose Dispute AI Decision.
            - Or press D, then press 1-5.

            The 1-5 labels mean:

            1. Best
            2. Strong
            3. Maybe
            4. Weak
            5. Reject

            Disputes are saved as adapter training labels with extra weight. That means they teach the adapter more strongly than a normal label, because they represent a clear disagreement between you and the AI.

            Use disputes for meaningful corrections, not every small preference difference. A focused set of good disputes can help the adapter learn quickly; noisy disputes can make the next adapter less stable.
            """,
        ),
        HelpPage(
            "Filters For Checking AI Results",
            """
            # Filters for checking AI results

            These filters help you inspect what the AI did:

            ## AI Ingested

            Shows images that made it into the main CLIP/TOPIQ scoring step.

            ## AI Prefilter Dumped

            Shows images that were quarantined or removed by DINO or pHash.

            ## DINO Quarantine

            Shows images that DINO marked for soft quarantine.

            ## DINO Removed

            Shows images that DINO removed from the scoring pool.

            ## AI Top Picks

            Shows the strongest current AI keep candidates.

            Use these filters to check whether the prefilters are helping or accidentally hiding useful images.
            """,
        ),
    )


def workflow_builder_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "What Workflow Recipes Are",
            """
            # What workflow recipes are

            A workflow recipe is a saved export or handoff setup.

            Recipes let you repeat the same output process without rebuilding the same settings every time. They are useful when you often copy, move, resize, convert, export, or archive images in the same way.

            Good uses include:

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

            Use this when you want polished final images, web-ready files, client previews, or edited deliverables.

            ## Full Bundle

            Works with the original selected image bundles.

            Use this when another person, program, or editing workflow needs the originals. This can keep RAW files, sidecars, and related files together depending on the transfer settings.

            In simple terms:

            - Use Export Deliverables when you want finished output files.
            - Use Full Bundle when you want to hand off or preserve the original files.
            """,
        ),
        HelpPage(
            "Transfer Mode",
            """
            # Transfer mode

            Transfer mode controls what happens to the selected files or bundles.

            ## Copy

            Leaves the original files where they are and creates a copy somewhere else.

            ## Move

            Moves the selected files to a new location. This changes where the originals are stored.

            ## Archive

            Packages the selected files or bundles into an archive file.

            When unsure, use Copy first. It is the safest option because it does not move or remove your originals.

            The preview panel shows what the recipe will do before you run it.
            """,
        ),
        HelpPage(
            "Saved Recipes",
            """
            # Saved recipes

            Save a recipe when you expect to use the same setup again.

            Built-in recipes are starting points.
            Saved recipes are your own custom presets.

            Even after selecting a saved recipe, you can still edit the fields before running it.
            """,
        ),
    )


def library_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "What The Library Panel Does",
            """
            # What the Library panel does

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

            Favorites are shortcuts to folders you use often.

            They do not move, copy, or change your files. They simply make frequently used folders easier to find and reopen.

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

            Virtual collections are saved groups of image references.

            They let you gather images together without moving or copying the original files.

            Use collections for things like:

            - Portfolio candidates
            - Client proofing sets
            - Images to edit later
            - Trip selects
            - Cross-folder themes
            - Final candidates
            - Review queues

            Removing an image from a collection does not delete the image from your computer.

            Deleting a collection also does not delete the original files.
            """,
        ),
        HelpPage(
            "Global Catalog",
            """
            # Global catalog

            The global catalog is an optional searchable index of selected folders.

            It lets you search filenames, paths, and cached image bundle information without manually opening each folder.

            This is useful for:

            - Finding older work
            - Rebuilding collections
            - Opening images from multiple folders
            - Quickly checking cached folder contents

            AI caches are still stored per folder. The catalog helps with searching and navigation, not with changing how folder-local AI processing works.
            """,
        ),
    )


def catalog_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "What The Catalog Is",
            """
            # What the catalog is

            The catalog is an optional index of folders you choose.

            It stores enough information to help you search and reopen image bundles quickly, without scanning every folder from scratch each time.

            The catalog does not move your files or force them into a new workflow. It is mainly a faster way to find and reopen existing work.
            """,
        ),
        HelpPage(
            "How To Use It",
            """
            # How to use it

            To use the catalog:

            1. Add one or more root folders from the Library menu.
            2. Refresh the catalog index.
            3. Search or browse the catalog by filename or path.
            4. Open results as a virtual catalog view.

            Catalog views are for discovery and navigation. They let you inspect found images without changing where the originals are stored.
            """,
        ),
        HelpPage(
            "When The Catalog Is Useful",
            """
            # When the catalog is useful

            The catalog is helpful when you want to:

            - Find images across multiple folders
            - Search old shoots
            - Build a collection from past work
            - Reopen a known project without manually browsing to it
            - Quickly inspect cached folder information

            For active editing inside one specific folder, normal folder browsing is usually the better choice.
            """,
        ),
    )


def collection_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "What Collections Are",
            """
            # What collections are

            A collection is a named group of image bundle references.

            Collections do not copy, move, or delete files. They simply remember where selected images are located so you can reopen that working set later.

            Think of a collection like a playlist for images: it points to files, but it does not contain the files themselves.
            """,
        ),
        HelpPage(
            "Good Uses For Collections",
            """
            # Good uses for collections

            Collections are useful for:

            - Portfolio candidates
            - Client proofing groups
            - Edit queues
            - Images to revisit later
            - Cross-folder themes
            - Final selects
            - Social media candidates
            - Comparison sets

            Choose a collection name that matches how you plan to use it later. For example, "Smith Wedding Proofs," "Portfolio Maybes," or "Colorado Trip Selects."
            """,
        ),
        HelpPage(
            "Collection Limits",
            """
            # Collection limits

            Collections depend on the original files staying in the same location.

            If images are moved, renamed, or deleted outside of Image Triage, the collection may show those items as missing.

            To fix missing items, move the files back, update their paths, or add the images to the collection again.
            """,
        ),
    )


def settings_help_pages() -> tuple[HelpPage, ...]:
    return (
        HelpPage(
            "What Settings Control",
            """
            # What settings control

            Settings are grouped by area so you can adjust different parts of the app without hunting through one giant list.

            The main settings areas are:

            ## General and Interface

            Controls the overall app behavior, layout, and display preferences.

            ## Library and Folders

            Controls folder loading, catalog cache behavior, and how the app handles image bundle data.

            ## AI

            Controls the main AI scoring system, including CLIP/TOPIQ scoring, adapter blending, and review bands.

            ## DINO Prefilter

            Controls the optional DINO first-pass filter that tries to detect likely rejects before full scoring.

            ## pHash Prefilter

            Controls duplicate or near-duplicate detection using perceptual hashing.
            """,
        ),
        HelpPage(
            "AI Settings",
            """
            # AI settings

            Core AI settings affect how images are scored, ranked, grouped, and reviewed.

            DINO and pHash settings affect which images reach the main scoring stage. Because these happen before the main AI ranking, changing them too aggressively can hide useful images from later steps.

            Start with conservative settings.

            After changing prefilter settings, check the results using:

            - AI Ingested
            - AI Prefilter Dumped
            - DINO Quarantine
            - DINO Removed
            - AI Top Picks

            These views help you confirm whether the AI is filtering the right images before you rely on it for a full triage pass.
            """,
        ),
    )
