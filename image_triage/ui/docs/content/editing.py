from __future__ import annotations

from ..model import DocArticle, DocCategory

CATEGORY = DocCategory(
    id="editing",
    title="Photo Editor",
    order=3,
    icon="✦",
    summary="Adjust a photo, make local masks, and save editable work or a finished copy.",
)

ARTICLES = [
    DocArticle(
        id="editor-overview",
        title="Using the Photo Editor",
        category="editing",
        summary="Open the popout viewer, choose an editing tool, and understand Save versus Save Copy.",
        keywords=("editor", "edit", "adjust", "crop", "retouch", "save", "save copy", "sidecar"),
        markdown="""
        # Using the Photo Editor

        Open a photo in the popout viewer with `Space` or `Enter`. The **Editor** button shows or hides the editing rail. The icons down the rail open these tools:

        - **Adjust** — tone, color, detail, and effects.
        - **Crop** — crop, straighten, rotate, and flip.
        - **Remove** — heal, spot-heal, or clone a distraction.
        - **Red Eye** — correct human red eye or pet eye.
        - **Masks** — apply adjustments to one part of the photo.
        - **Background** — blur or remove the background.
        - **Lens Blur** — add depth-of-field blur.
        - **Presets** — apply or save a reusable editing look.

        Most changes update the preview as you work. They do not immediately rewrite the source photo.

        ## Save and Save Copy

        **Save** stores the editing instructions in Image Triage's sidecar session. The original pixels stay unchanged, and you can reopen the photo to keep editing.

        **Save Copy** first saves the editable instructions, then renders a new image at the location and format you choose. Use this when you need a finished file for delivery or another program.

        **Reset** clears the current tonal and color adjustments. Crop and retouch work have their own reset controls so a general adjustment reset does not silently discard that work.
        """,
    ),
    DocArticle(
        id="editor-masks",
        title="Masks and local adjustments",
        category="editing",
        summary="Select part of a photo, refine the selection, and adjust only that area.",
        keywords=("mask", "subject", "people", "sky", "scene", "brush", "gradient", "color range", "refine"),
        markdown="""
        # Masks and local adjustments

        A mask limits an adjustment to part of the photo. For example, you can brighten a face without brightening the background, cool the sky without changing skin tones, or reduce detail behind the subject.

        Open **Masks** from the editor rail, choose **New Mask**, then choose a method. AI-assisted choices can identify subjects and scene regions. Manual tools include Brush, Linear gradient, Radial gradient, and Color Range.

        ## A dependable workflow

        1. Choose a mask method and follow the instruction shown above the controls.
        2. Check the colored overlay to see exactly what is selected.
        3. Use **Add** or **Subtract** when the first selection missed an area or included too much.
        4. Use **Refine Edges** or the touch-up tools when hair, fur, clothing, or a complex edge needs care.
        5. Adjust tone, color, detail, or effects for that mask.
        6. Use **Invert** when you need everything outside the selected area.

        The overlay is only a selection guide; it does not appear in the saved photograph. You can change its viewing mode when the photo colors make the selection hard to see.

        ## First use and model downloads

        Subject and scene masks use local AI models. The first attempt may need to prepare or download those files and can take longer than later attempts. If setup fails, use **`AI > AI Setup And Cache > Repair AI...`**, then reopen the editor and try again. Manual brush and gradient masks remain useful when an automatic selection is not appropriate.
        """,
    ),
]
