from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="getting-started",
    title="Getting Started",
    order=0,
    icon="\U0001F680",
    summary="Open a folder, learn the window, and make your first cull.",
)

ARTICLES = [
    DocArticle(
        id="welcome",
        title="Welcome to Image Triage",
        category="getting-started",
        summary="A fast, keyboard-driven photo culling tool with optional AI assistance.",
        keywords=("intro", "overview", "start", "home", "about"),
        markdown="""
        # Welcome to Image Triage

        Image Triage helps you turn a large shoot into a clean set of keepers — quickly, and without losing control of your files.

        It is built around three ideas:

        - **Speed** — review and sort hundreds of images with the keyboard, not the mouse.
        - **Safety** — nothing is moved, copied, or deleted unless you ask for it.
        - **Optional AI** — let the app score and pre-sort a folder, then learn your taste over time. AI suggests; you decide.

        ## Where to go next

        - New here? Start with [Your first cull](doc:first-cull).
        - Want the lay of the land? See [The main window](doc:window-tour).
        - Ready to move fast? Read [Working keyboard-first](doc:keyboard-first).
        - Curious about the AI? Begin with [How AI culling works](doc:how-ai-works).

        > **Tip:** Every dialog with a **`?`** button has focused help for that screen. This Documentation window is the full reference behind those buttons.
        """,
    ),
    DocArticle(
        id="window-tour",
        title="The main window",
        category="getting-started",
        summary="A quick tour of the grid, preview, library panel, and menus.",
        keywords=("layout", "interface", "ui", "panels", "tour"),
        markdown="""
        # The main window

        The main window is organized so the images stay front and center.

        ## The image grid

        The grid is your workspace. It shows thumbnails for the current folder with heart/X culling controls. Select images here, then act on them with the keyboard, the right-click menu, or the toolbar.

        ## Preview

        Press `Space` or `Enter` to open the full-size preview. Preview supports zoom, a loupe, and side-by-side compare. See [Preview, zoom & loupe](doc:preview).

        ## The Library panel

        The Library panel helps you navigate and reopen folders, manage favorites, and build virtual collections across locations. See [The Library panel](doc:library-overview).

        ## The menu bar

        - **File** — open folders and manage the session.
        - **View** — appearance, layout, sorting, filters, and burst views.
        - **Review** — culling, preview, and selection tools.
        - **Library / Workflow** — collections, the catalog, and export recipes.
        - **AI** — the AI Workflow Center, adapter training, and result filters.
        - **Tools** — batch rename, resize, convert, and archive.
        - **Help** — this documentation, guides, and updates.

        ## The status bar

        The status bar at the bottom reports what just happened — how many images were sorted, what a long task is doing, and any warnings worth noticing.
        """,
    ),
    DocArticle(
        id="first-cull",
        title="Your first cull",
        category="getting-started",
        summary="The fastest path from opening a folder to a sorted set.",
        keywords=("quick start", "tutorial", "workflow", "sort", "cull"),
        markdown="""
        # Your first cull

        This is the shortest path from a fresh folder to a sorted set.

        1. **Open a folder** — `File > Open Folder...`.
        2. **Select images** — click, `Ctrl`-click, `Shift`-click, or drag to marquee-select. See [Selecting images](doc:selecting).
        3. **Cull quickly** — `W` marks winners, `X` rejects, `K` moves to `_keep`, `M` moves, `Delete` trashes.
        4. **Preview when unsure** — `Space` or `Enter` opens the full view.
        5. **Run batch actions** — right-click or the **Tools** menu for rename, resize, convert, and archive.
        6. **Organize by drag and drop** — drop onto folders or favorites; hold `Ctrl` to copy instead of move.

        ## Made a mistake?

        Press `Ctrl+Z` to undo the last change. Image Triage is built to be forgiving — sort fast and correct as you go.

        ## Add AI to the mix

        Once you are comfortable, let the app pre-sort a folder for you. Open **`AI > AI Workflow Center...`** and read [How AI culling works](doc:how-ai-works).
        """,
    ),
    DocArticle(
        id="keyboard-first",
        title="Working keyboard-first",
        category="getting-started",
        summary="The core habits that make culling fast.",
        keywords=("keyboard", "shortcuts", "speed", "flow", "hotkeys"),
        markdown="""
        # Working keyboard-first

        Image Triage is fastest when your hands stay on the keyboard. A few habits make the biggest difference.

        ## The core verbs

        - `W` marks winners, `X` rejects.
        - `K` moves to `_keep`; `M` moves to a chosen folder.
        - `T` tags.
        - `Delete` trashes; `Ctrl+Z` undoes.

        ## Look closer without slowing down

        - `Space` or `Enter` opens Preview; arrow keys move between images.
        - `Z` zooms, `0` returns to fit, `L` toggles the loupe, `C` toggles compare.

        ## Move through the set

        - `Ctrl+A` selects all visible images.
        - `Ctrl+Shift+X` clears active filters.
        - `[` and `]` cycle through a burst in the viewer.

        For the complete list, see [Keyboard shortcuts](doc:shortcuts).
        """,
    ),
]
