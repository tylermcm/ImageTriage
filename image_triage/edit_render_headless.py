"""Headless rendering of a built-in editor session (no live ``PhotoEditorPanel``).

WI-5.3 (D3, unify has_edits): today the built-in editor's edits are only
visible inside the live popout editor -- thumbnails, the grid, and every
export path decode and show the unedited original. This module is the shared
foundation stage 3/4/5 will build on to change that: given a source image and
its saved ``.image_triage_edits`` session, render the edited pixels the same
way ``editor_copy.write_edited_copy`` already does for "Save Copy", but
without needing a live widget.

It reuses:
- ``edit_storage.resolve_session_for_read`` / the same "operations or masks
  non-empty" definition of "has real edits" as ``edit_storage.session_has_edits``,
  so the two never drift out of sync.
- ``edit_session_geometry.build_masked_adjustments`` for mask-group assembly
  (the same helpers the live editor panel now delegates to).
- ``photo_editor_panel.recipe_from_session`` for the global recipe (already a
  module-level free function, not an instance method).
- ``editor_render.CpuEditorRenderBackend`` for the actual pixel render (the
  same backend ``write_edited_copy`` uses).

Known limitations (deliberate, documented rather than silently worked around):

1. ``EditRecipe.background_mode`` / ``lensblur_amount`` are not currently
   written to the saved session schema at all -- ``photo_editor_panel.
   SESSION_OPS`` has no entry for either field, so ``operations_from_recipe``
   never persists them and ``recipe_from_session`` always reads them back as
   the "off" default. That is a pre-existing gap unrelated to WI-5.3. The
   background/lensblur spec builders below are implemented for forward
   compatibility (and are exercised directly in tests with a hand-built
   recipe), but for any session saved by today's app they are effectively
   dead code: there is currently no way for a saved session to carry a
   non-off background/lens-blur setting.
2. Even once (1) is fixed, when the subject matte / depth map has not been
   computed yet, this module does NOT trigger that computation -- that would
   mean running a full ML inference pass synchronously inside a thumbnail or
   export call, which is never appropriate here. That specific effect is
   silently skipped (the recipe still carries the requested mode/amount; the
   rendered pixels just do not show it yet) while the rest of the edit
   (global + masked adjustments) still renders normally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from . import edit_storage
from .edit_session_geometry import build_masked_adjustments
from .image_ops import load_image_for_transform
from .photo_terminal.session import load_session

_BACKGROUND_MATTE_MODES = ("blur", "color", "remove")


def _session_has_real_edits(session: dict[str, Any]) -> bool:
    """Same definition as ``edit_storage.session_has_edits`` -- kept in one
    place there and mirrored here on the already-loaded dict so the two
    cannot silently diverge."""

    return bool(session.get("operations")) or bool(session.get("masks"))


def _background_render_spec(recipe: Any, source_path: Path) -> dict | None:
    mode = str(getattr(recipe, "background_mode", "off"))
    if mode not in _BACKGROUND_MATTE_MODES:
        return None
    try:
        from .subject_masks import subject_mask_cache_path

        matte = subject_mask_cache_path(source_path, "subject")
    except Exception:
        matte = None
    if matte is None:
        # Not cached yet. Never trigger BiRefNet inference from here -- see
        # module docstring, limitation 2. Skip the effect, not the render.
        return None
    return {
        "mode": mode,
        "amount": float(getattr(recipe, "background_amount", 60.0)),
        "color": str(getattr(recipe, "background_color", "#000000")),
        "matte_path": str(matte),
    }


def _lensblur_render_spec(recipe: Any, source_path: Path) -> dict | None:
    amount = float(getattr(recipe, "lensblur_amount", 0.0))
    if amount <= 0.0:
        return None
    try:
        from .depth_maps import depth_map_cache_path

        depth = depth_map_cache_path(source_path)
    except Exception:
        depth = None
    if depth is None:
        # Not cached yet. Never trigger depth-model inference from here --
        # see module docstring, limitation 2.
        return None
    return {
        "amount": amount,
        "focus": float(getattr(recipe, "lensblur_focus", 0.7)),
        "depth_path": str(depth),
    }


def render_session_pixels(
    source_path: str | Path,
    recipe: Any,
    masked_adjustments: list,
    *,
    background: dict | None = None,
    lensblur: dict | None = None,
    target_size: QSize | None = None,
    base_key: tuple | None = None,
) -> tuple[QImage, bytes | None, bytes | None]:
    """Decode ``source_path`` and render it through an already-assembled
    ``(recipe, masked_adjustments, background, lensblur)``.

    This is the shared decode+render core behind both ``render_edited_image``
    (below, which assembles those four from a saved session) and
    ``editor_copy.write_edited_copy`` (which receives them already assembled
    from the live editor panel, for "Save Copy"). Unlike ``render_edited_image``,
    this raises ``OSError`` on decode/render failure instead of swallowing it,
    since ``write_edited_copy``'s callers need to surface that error to the
    user.

    Returns ``(rendered_image, exif_bytes, icc_profile)``.
    """

    source = Path(source_path)
    loaded = load_image_for_transform(
        str(source),
        target_size=target_size if target_size is not None else QSize(),
        ignore_orientation=False,
        strip_metadata=False,
    )
    if loaded.image.isNull():
        raise OSError(f"Could not decode {source.name}.")

    from .editor_render import CpuEditorRenderBackend

    rendered = CpuEditorRenderBackend().render(
        loaded.image,
        recipe,
        masked_adjustments,
        base_key=base_key or ("render-session-pixels", str(source.resolve(strict=False))),
        background=background,
        lensblur=lensblur,
        # A saved copy or a headless render always shows the real crop,
        # whatever tool happens to be armed in a live editor for this image.
        view={"bypass_crop": False},
    )
    if rendered.isNull():
        raise OSError(f"Could not render {source.name}.")
    return rendered, loaded.exif_bytes, loaded.icc_profile


def render_edited_image(
    source_path: str | Path,
    *,
    target_size: QSize | None = None,
) -> QImage | None:
    """Render ``source_path`` through its saved editor session, headlessly.

    Returns None when there is no session, the session is unreadable, or
    nothing was actually edited (an opened-but-untouched session, matching
    ``edit_storage.session_has_edits``). Never raises: callers such as a
    thumbnail worker must be able to fall back to the plain decode on any
    failure.

    ``target_size`` is forwarded to the decode (``image_ops.
    load_image_for_transform``) so a thumbnail caller can request a
    downscaled decode instead of always paying for a full-resolution one.
    Pass None (the default) for a full-resolution render, matching
    ``editor_copy.write_edited_copy``'s behavior.
    """

    try:
        source = Path(source_path)
        session_path = edit_storage.resolve_session_for_read(source)
        if not session_path.exists():
            return None
        session = load_session(session_path)
        if not _session_has_real_edits(session):
            return None

        # Deferred import: photo_editor_panel is a heavy UI module and (via
        # edit_session_geometry) ends up importing this module's sibling
        # helpers, so importing it back at top level here risks a cycle. The
        # same lazy-import pattern is used elsewhere in this codebase for the
        # same reason (see editor_copy.write_edited_copy's import of
        # CpuEditorRenderBackend).
        from .ui.photo_editor_panel import recipe_from_session

        recipe = recipe_from_session(session)
        masked_adjustments = build_masked_adjustments(session, session_path, source)
        background = _background_render_spec(recipe, source)
        lensblur = _lensblur_render_spec(recipe, source)

        rendered, _exif_bytes, _icc_profile = render_session_pixels(
            source,
            recipe,
            masked_adjustments,
            background=background,
            lensblur=lensblur,
            target_size=target_size,
            base_key=("headless-render", str(source.resolve(strict=False))),
        )
        return rendered
    except Exception:
        return None
