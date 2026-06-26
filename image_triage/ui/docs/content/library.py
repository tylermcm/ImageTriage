from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="library",
    title="Library & Organization",
    order=4,
    icon="\U0001F4DA",
    summary="Navigate folders, save favorites, build collections, and search a catalog.",
)

ARTICLES = [
    DocArticle(
        id="library-overview",
        title="The Library panel",
        category="library",
        summary="Navigate, organize, and quickly return to image folders.",
        keywords=("library", "panel", "navigate", "favorites", "collections", "catalog"),
        markdown="""
        # The Library panel

        The Library panel helps you navigate, organize, and quickly return to image folders.

        It includes:

        - [Favorites](doc:favorites) — shortcuts to folders you use often.
        - Folder browsing — move through your drives and directories.
        - [Virtual collections](doc:collections) — saved image sets that span folders.
        - [The global catalog](doc:catalog) — a searchable index of chosen folders.

        The Library does not replace normal folder browsing. It simply makes it easier to reopen important folders and assemble image sets across different locations.
        """,
    ),
    DocArticle(
        id="favorites",
        title="Favorites",
        category="library",
        summary="Shortcuts to folders you open often.",
        keywords=("favorite", "favorites", "shortcut", "pin", "quick access"),
        markdown="""
        # Favorites

        Favorites are shortcuts to folders you use often. They do not move, copy, or change your files — they just make frequently used folders easier to find and reopen.

        Good candidates for favorites:

        - Current projects
        - Client folders
        - Import folders
        - Export folders
        - Common editing locations

        > **Tip:** You can drag selected images onto a favorite to move them there — hold `Ctrl` to copy instead. See [Moving, keeping & trashing](doc:moving-keeping).
        """,
    ),
    DocArticle(
        id="collections",
        title="Virtual collections",
        category="library",
        summary="Saved groups of image references that span folders.",
        keywords=("collection", "collections", "virtual", "set", "playlist", "group"),
        markdown="""
        # Virtual collections

        A virtual collection is a named group of image references. Collections let you gather images together **without moving or copying** the original files.

        Think of a collection as a playlist for images: it points to files, but it does not contain them.

        ## Good uses

        - Portfolio candidates
        - Client proofing sets
        - Images to edit later
        - Trip selects and cross-folder themes
        - Final candidates and review queues

        Choose a name that matches how you will use the set later — for example, "Smith Wedding Proofs" or "Portfolio Maybes."

        ## What collections never do

        Removing an image from a collection does not delete it from your computer, and deleting a collection does not delete the original files.

        > **Note:** Because collections point at files by location, moving or renaming those files outside Image Triage can leave a collection showing items as missing. Re-add them, or restore the original paths, to fix it.
        """,
    ),
    DocArticle(
        id="catalog",
        title="The global catalog",
        category="library",
        summary="An optional searchable index of folders you choose.",
        keywords=("catalog", "index", "search", "global", "find"),
        markdown="""
        # The global catalog

        The global catalog is an optional, searchable index of folders you choose. It lets you search filenames, paths, and cached image information without opening each folder by hand.

        ## How to use it

        1. Add one or more root folders from the **Library** menu.
        2. Refresh the catalog index.
        3. Search or browse by filename or path.
        4. Open results as a virtual catalog view.

        ## When it helps

        - Finding older work across many folders
        - Rebuilding a collection from past shoots
        - Reopening a known project without browsing to it manually

        > **Note:** AI caches are still stored per folder. The catalog helps with search and navigation — it does not change how folder-local AI processing works. See [Where AI files live](doc:where-files-live).
        """,
    ),
]
