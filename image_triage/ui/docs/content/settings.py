from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="settings",
    title="Settings",
    order=6,
    icon="⚙",
    summary="Tune review behavior, the interface, folders, AI culling, duplicates, and shortcuts.",
)

ARTICLES = [
    DocArticle(
        id="settings-overview",
        title="Settings overview",
        category="settings",
        summary="What each settings page controls and when a change takes effect.",
        keywords=("settings", "preferences", "options", "configure", "general", "interface"),
        markdown="""
        # Settings overview

        Open Settings with **`Settings > Settings...`** or `Ctrl+,`. The pages on the left keep related choices together:

        - **General** — review presets, what accepting an image does, where deleted images go, and update checks.
        - **Interface** — card appearance, interface brightness, scrolling, preview preloading, and review navigation.
        - **Library & Folders** — the folder tree, automatic refresh, and catalog cache.
        - **AI Culling** — processing load and the winner, review, and reject ranges.
        - **Duplicates** — the optional pHash check for tight visual repeats.
        - **Shortcuts** — keyboard bindings for common commands.

        Click **OK** to save changes or **Cancel** to leave the current settings in place. Settings that affect the main window are applied after you click OK. Use **Settings Guide** at the bottom-left for a shorter guide inside the dialog.

        ## Theme and layout

        Choose a color theme from **`View > Appearance`**. New installations start with **Graphite**. Panel visibility and placement live under **`View > Panels`** and **`View > Panel Layout`**; **`View > Reset Window Layout`** restores the standard workspace.
        """,
    ),
    DocArticle(
        id="interface-settings",
        title="Interface and review settings",
        category="settings",
        summary="Understand card styles, gamma, preview loading, and automatic review movement.",
        keywords=("interface", "card style", "gamma", "scroll", "preview", "auto advance", "bursts"),
        markdown="""
        # Interface and review settings

        ## Appearance

        **Card style** changes the information shown around each thumbnail. Detailed keeps filenames, image details, and status visible. Gallery uses a compact caption. Zen removes most labels so the photographs get the most space.

        **UI gamma** brightens or darkens the whole interface to compensate for a monitor that makes dark shades hard to separate. It does not alter the photographs or exported files. Use Reset to return to the designed value of 1.00.

        ## Navigation and preview

        **Free smooth scrolling** moves the grid by small amounts instead of snapping by rows. **Preview preload** prepares nearby images while the popout viewer is open. A larger preload can make rapid Left/Right navigation smoother, but uses more memory. Set it to Off if memory is tight.

        **Show hidden folders** includes Windows hidden folders and dot folders in the folder tree. Leave it off if you only want normal photo folders visible.

        ## Review flow

        **Advance after Accept or Reject** selects the next image after a decision. **Group burst sequences** identifies frames captured close together. **Stack similar burst frames** collapses a similar run behind one representative so the grid stays manageable. These views do not move or delete files.
        """,
    ),
    DocArticle(
        id="ai-settings",
        title="AI culling settings",
        category="settings",
        summary="Tune processing load and divide a ranking into useful review ranges.",
        keywords=("ai settings", "workers", "processing", "keep top", "winner", "review band", "threshold"),
        markdown="""
        # AI culling settings

        **Processing workers** controls how many images the AI prepares at the same time. Auto chooses a balanced value for the computer. Raise it only when processing is slow and the computer still has spare memory and processor capacity.

        **Detailed progress log** shows model loading and per-stage activity in the AI Review progress window. It helps with troubleshooting, but adds detail most people do not need during a normal run.

        ## Result ranges

        **Likely winners** is the top portion of the finished ranking. At 10%, roughly the highest-ranked ten images out of every hundred enter that range.

        **Review band** is the portion immediately below likely winners that the app sets aside for a human decision. A 10% winner range plus a 10% review band leaves the remaining 80% in the likely-reject range. These percentages divide an existing ranking; they do not retrain the models.

        The summary row shows the current split before you save it. A smaller winner range is more selective. A larger review band asks you to inspect more borderline images.
        """,
    ),
    DocArticle(
        id="duplicate-settings",
        title="Duplicate settings",
        category="settings",
        summary="Use perceptual hashing to identify near-identical frames before AI scoring.",
        keywords=("duplicates", "phash", "perceptual hash", "distance", "cache", "diagnostics", "prefilter"),
        markdown="""
        # Duplicate settings

        The duplicate check uses **pHash**, short for perceptual hash. It turns the visible appearance of each image into a small fingerprint, then compares those fingerprints to find tight repeats.

        **Enable pHash Prefilter** runs this check before the slower AI scoring pass. Duplicate candidates stay in the folder and remain available for manual review. An image you already marked as a winner is protected and preferred as the representative of its group.

        **Duplicate distance** controls how different two fingerprints may be while still counting as duplicates. Lower values are stricter. The default value of 6 aims at very close repeats rather than merely similar poses. Change it in small steps and audit the result before applying AI decisions.

        **Cache pHash metadata** saves only the fingerprints so later runs can avoid repeating work. It does not copy or cache the image files. **Run diagnostics** writes duplicate groups and decision rows for troubleshooting; leave it off unless you need to inspect the prefilter.

        Check **`AI > Results And Filters > Prefilter / Ingest`** after a change. **AI Ingested** shows what reached full scoring, while **AI Prefilter Dumped** shows what the duplicate check held out.
        """,
    ),
    DocArticle(
        id="library-folder-settings",
        title="Library and folder settings",
        category="settings",
        summary="Control folder-tree expansion, file watching, and the catalog cache.",
        keywords=("library settings", "folder tree", "watch folder", "catalog cache", "refresh"),
        markdown="""
        # Library and folder settings

        **Keep only one branch expanded per level** collapses neighboring branches as you open the folder tree. The active path stays open, which reduces clutter on computers with many drives and folders.

        **Refresh the open folder when files change on disk** watches for files added, removed, or renamed by another program. Turn it off only when an external program is making many changes and you prefer to refresh manually with `F5`.

        **Use catalog cache for faster folder open** stores lightweight information about indexed image bundles. The status below it reports what the catalog currently knows. This cache speeds browsing; it does not relocate originals or contain the AI models. Use **`Library > Catalog > Rebuild Open Folder Cache`** if a folder's cached contents appear stale.
        """,
    ),
]
