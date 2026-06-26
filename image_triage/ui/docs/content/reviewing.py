from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="reviewing",
    title="Reviewing & Sorting",
    order=1,
    icon="\U0001F5BC",
    summary="Select, rate, move, preview, compare, and group images.",
)

ARTICLES = [
    DocArticle(
        id="selecting",
        title="Selecting images",
        category="reviewing",
        summary="Click, range-select, marquee-select, and select all.",
        keywords=("select", "selection", "marquee", "multi-select"),
        markdown="""
        # Selecting images

        Most actions apply to the current selection, so selecting well is the foundation of fast sorting.

        - **Click** selects a single image.
        - **`Ctrl`-click** adds or removes an image from the selection.
        - **`Shift`-click** selects a range.
        - **Drag on empty space** marquee-selects, just like File Explorer.
        - **`Ctrl+A`** selects every visible image.

        ## Selection and filters

        The selection only ever covers *visible* images. When a filter is active, actions stay scoped to what you can see — a safe way to work through one slice of a folder at a time. Clear filters with `Ctrl+Shift+X`.

        ## Drag to organize

        Drag selected thumbnails onto folders or favorites to move them. Hold `Ctrl` while dragging to copy instead. See [Moving, keeping & trashing](doc:moving-keeping).
        """,
    ),
    DocArticle(
        id="rating-sorting",
        title="Rating, accepting & rejecting",
        category="reviewing",
        summary="Star ratings, accept/reject flags, and tags.",
        keywords=("rate", "rating", "stars", "accept", "reject", "flag", "tag"),
        markdown="""
        # Rating, accepting & rejecting

        Image Triage gives you several ways to mark an image. Use whichever fits how you think about your work.

        ## Accept and reject

        - `W` **accepts** the selected images.
        - `X` **rejects** them.

        Accept/reject is the fastest first pass: a binary in-or-out decision you can refine later.

        ## Star ratings

        Press `0`–`5` to rate. Ratings are ideal for ranking keepers against each other once the obvious rejects are gone.

        ## Tags

        Press `T` to tag the selection. Tags are freeform labels for grouping images by theme, subject, or any workflow you like.

        > **Note:** Ratings, accept/reject, and tags are independent. An image can be accepted *and* rated *and* tagged at the same time.

        Ratings also feed AI training when you build an adapter — see [What adapters are](doc:what-adapters-are).
        """,
    ),
    DocArticle(
        id="moving-keeping",
        title="Moving, keeping & trashing",
        category="reviewing",
        summary="Move to _keep, move to a folder, trash, and undo.",
        keywords=("move", "keep", "trash", "delete", "undo", "file"),
        markdown="""
        # Moving, keeping & trashing

        Sorting ultimately means putting files where they belong. These actions do that safely.

        - `K` moves the selection to a **`_keep`** subfolder.
        - `M` moves the selection to a folder you choose.
        - `Delete` sends the selection to the trash.
        - `Ctrl+Z` undoes the last change.

        ## Recent destinations

        Folders you have moved or copied to recently appear in the copy and move menus, so repeat destinations are one click away.

        ## Copy versus move

        Dragging thumbnails onto a folder **moves** them. Hold `Ctrl` while dragging to **copy** instead, leaving the originals in place.

        > **Tip:** Nothing leaves your control silently. Moves, copies, and deletes only happen when you trigger them, and `Ctrl+Z` reverses the last one.
        """,
    ),
    DocArticle(
        id="preview",
        title="Preview, zoom & loupe",
        category="reviewing",
        summary="Open the full-size view and inspect detail.",
        keywords=("preview", "zoom", "loupe", "fit", "detail", "focus"),
        markdown="""
        # Preview, zoom & loupe

        When a thumbnail is not enough, open the full-size preview.

        - `Space` or `Enter` opens Preview.
        - Mouse wheel or `Z` zooms in.
        - `0` returns to fit.
        - `L` toggles the loupe for a magnified spot check.
        - Left and Right navigate between images.
        - `Tab` changes preview focus.

        ## Check your edits

        **Before/After** compares the original with the latest detected edit, so you can confirm an adjustment landed the way you expected.

        ## Hand off to your editor

        **Open In Photoshop** sends the current preview image straight to Photoshop when you are ready to work on a keeper.
        """,
    ),
    DocArticle(
        id="compare",
        title="Comparing & before/after",
        category="reviewing",
        summary="Compare similar frames side by side to pick the best.",
        keywords=("compare", "side by side", "before after", "choose", "best"),
        markdown="""
        # Comparing & before/after

        When several frames are nearly identical, compare them directly instead of guessing.

        - `C` toggles **compare** in the grid and in Preview.
        - In Preview, compare shows images side by side so small differences in focus, expression, or timing stand out.
        - **Before/After** compares an image against its latest detected edit.

        ## Comparing AI groups

        When AI results are loaded, the app groups visually similar shots for you. Press `Ctrl+Alt+G` to compare the current AI group and pick the winner quickly. See [Reviewing AI results](doc:ai-review).
        """,
    ),
    DocArticle(
        id="bursts",
        title="Burst groups & stacks",
        category="reviewing",
        summary="Tag and navigate rapid-fire capture sequences.",
        keywords=("burst", "stack", "sequence", "continuous", "group"),
        markdown="""
        # Burst groups & stacks

        Continuous shooting produces long runs of near-identical frames. Burst views help you collapse that noise.

        ## Burst Groups

        **`View > Burst Groups`** highlights likely capture bursts in the grid. It is a *toggle* — a way to see sequences at a glance — not a permanent regrouping of your folder.

        ## Burst Stacks

        **`View > Burst Stacks`** adds a stacked, stack-style burst visual and lets you cycle through a burst in the main viewer with `[` and `]`. It is the fastest way to step through a sequence and keep only the best frame.

        > **Note:** Burst grouping is based on capture timing and similarity. The AI's perceptual-hash prefilter handles tighter near-duplicate detection during scoring — see [Prefilters: DINO & pHash](doc:prefilters).
        """,
    ),
]
