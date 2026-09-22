from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="export",
    title="Export & Handoff",
    order=5,
    icon="\U0001F4E6",
    summary="Save repeatable recipes to export, copy, move, or archive your keepers.",
)

ARTICLES = [
    DocArticle(
        id="pocketdrop",
        title="PocketDrop",
        category="export",
        summary="Move files and notes between this computer and your phone by scanning a QR code.",
        keywords=("pocketdrop", "phone", "qr", "share", "send", "transfer", "wifi", "anywhere", "offline", "receive"),
        markdown="""
        # PocketDrop

        **PocketDrop** moves files and text between this computer and a phone, in either direction, by scanning a QR code. The phone needs no app and no account. Open it from the **PocketDrop** button on the left rail.

        ## Send to your phone

        1. Select images in the grid and choose **Workflow > Send to PocketDrop** (Ctrl+Alt+P), or drop files, folders or text onto the PocketDrop panel. **Add files**, **Folder** and **Paste** (Ctrl+V) work too.
        2. Scan the QR code with the phone's camera.
        3. Download single files, or **Download all** as one zip that keeps folder structure.

        The original files are shared as they are. Nothing is copied, converted or uploaded to a cloud service.

        ## Receive from your phone

        The phone page has **Send to** this computer: pick photos, videos, any files, or a note. Received files are saved to `Downloads/PocketDrop` (change it from the **⋯** menu) and appear at the top of the panel. Click a file to show it in its folder, or a note to copy it.

        ## Ways to connect

        - **Same Wi-Fi**: a direct transfer over your local network, and the fastest.
        - **Anywhere**: a temporary Cloudflare link, so a phone on mobile data can connect. Anyone with an active Anywhere link can download what you share until it expires, so PocketDrop asks before turning it on. Sending files back to this computer needs the separate receive code shown in the panel.
        - **Offline** (Windows): PocketDrop creates a temporary Wi-Fi network to join first, falling back to Bluetooth when the PC has no Wi-Fi adapter.

        ## Links and privacy

        Every link carries a random token and changes on its own: when PocketDrop starts, every minute while nothing is shared, and after 15 minutes without phone activity. **New link** in the **⋯** menu revokes the current one immediately, and links end when Image Triage closes.

        If a phone on the same Wi-Fi can't connect, Windows may be blocking the connection. Allow Image Triage on **Private** networks when Windows asks, and make sure your network profile is set to Private.
        """,
    ),
    DocArticle(
        id="recipes",
        title="Workflow recipes",
        category="export",
        summary="Saved export or handoff setups you can reuse.",
        keywords=("recipe", "workflow", "export", "handoff", "preset", "deliver"),
        markdown="""
        # Workflow recipes

        A workflow recipe is a saved export or handoff setup. Recipes let you repeat the same output process without rebuilding the settings each time.

        They are useful whenever you copy, move, resize, convert, export, or archive images the same way — for example:

        - Client delivery folders
        - Editor handoff folders
        - Proofing sets and social media exports
        - Final archives and backup copies

        A recipe is defined by two main choices: [content mode](doc:content-mode) (what to produce) and [transfer mode](doc:transfer-mode) (what to do with it).

        ## Built-in vs. saved recipes

        Built-in recipes are ready-made starting points. Saved recipes are your own presets. Even after selecting a saved recipe, you can still edit the fields before running it.

        > **Tip:** The preview panel shows exactly what a recipe will do before you run it.
        """,
    ),
    DocArticle(
        id="content-mode",
        title="Content mode",
        category="export",
        summary="Choose between finished deliverables and original bundles.",
        keywords=("content mode", "deliverables", "bundle", "export", "originals"),
        markdown="""
        # Content mode

        Content mode controls what kind of output a recipe creates.

        ## Export Deliverables

        Creates new output files using your chosen resize, conversion, metadata, and filename settings. Use it for polished final images, web-ready files, client previews, or edited deliverables.

        ## Full Bundle

        Works with the original selected image bundles. Use it when another person, program, or editing workflow needs the originals. Depending on the transfer settings, this can keep RAW files, sidecars, and related files together.

        > **In short:** choose **Export Deliverables** for finished output files, and **Full Bundle** to hand off or preserve the originals.

        Pair this with a [transfer mode](doc:transfer-mode) to decide whether files are copied, moved, or archived.
        """,
    ),
    DocArticle(
        id="transfer-mode",
        title="Transfer mode",
        category="export",
        summary="Copy, move, or archive the selected files or bundles.",
        keywords=("transfer mode", "copy", "move", "archive", "zip"),
        markdown="""
        # Transfer mode

        Transfer mode controls what happens to the selected files or bundles.

        - **Copy** — leaves the originals in place and creates a copy elsewhere.
        - **Move** — relocates the selected files, changing where the originals are stored.
        - **Archive** — packages the selected files or bundles into an archive file.

        > **Tip:** When unsure, start with **Copy**. It is the safest option because it never moves or removes your originals. The preview panel shows what the recipe will do before you run it.
        """,
    ),
]
