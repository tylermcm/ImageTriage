"""The popout's RAW draft and its private PhotoCraft render jobs."""
from __future__ import annotations

import math
from copy import deepcopy
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot

from .. import atmosphere, bokeh, edit_storage, host_effects, light_depth, photocraft_bridge as bridge
from ..formats import EDITOR_SUFFIXES
from .histograms import HistogramService
from . import raw_preview
from ..raw_crop import DEFAULT_CROP, RATIOS, bounds as crop_bounds, valid as crop_valid

_native_context = threading.local()


class _NativeJob:
    def __init__(self):
        self.stopped = threading.Event()
        self.proc = None

    def cancel(self):
        self.stopped.set()
        if self.proc and self.proc.process.poll() is None:
            try:
                self.proc.process.kill()
            except OSError:
                pass


def _image_url(path):
    return path if path.startswith("image://") else QUrl.fromLocalFile(path).toString() if path else ""


FIELDS = {"exposure": (-5, 5), **dict.fromkeys(
    ("contrast", "highlights", "shadows", "whites", "blacks", "temperature", "tint", "vibrance", "saturation",
     "curveHighlights", "curveLights", "curveDarks", "curveShadows", "gradeBalance",
     "texture", "clarity", "dehaze", "vignetteAmount", "vignetteRoundness", "calibShadowTint"),
    (-100, 100),
)}
FIELDS["gradeBlending"] = (0, 100)
FIELDS.update(dict.fromkeys(
    ("vignetteMidpoint", "vignetteFeather", "vignetteHighlights", "grainAmount", "grainSize", "grainRoughness",
     "sharpenDetail", "sharpenMasking", "noiseLuminance", "noiseLuminanceDetail", "noiseColor", "noiseColorDetail"),
    (0, 100),
))
FIELDS.update(sharpenAmount=(0, 150), sharpenRadius=(0.5, 3))
CURVES = ("pointCurve", "pointCurveRed", "pointCurveGreen", "pointCurveBlue")
MIXER = ("hslHue", "hslSat", "hslLum")
WHEELS = ("gradeShadows", "gradeMidtones", "gradeHighlights", "gradeGlobal")
DEFAULTS = {
    "masks": [],
    "retouch": [],
    "redEye": [],
    "sky": {},
    "hostCrop": DEFAULT_CROP,
    "lightDepth": dict(light_depth.DEFAULTS),
    "atmosphere": dict(atmosphere.DEFAULTS),
    "bokeh": dict(bokeh.DEFAULTS),
    "pointColors": [],
    **dict.fromkeys(FIELDS, 0),
    "gradeBlending": 50,
    "curveSplits": [25, 50, 75],
    "vignetteMidpoint": 50, "vignetteFeather": 50, "vignetteStyle": "highlightPriority",
    "grainSize": 25, "grainRoughness": 50,
    "glow": {"amount": 0, "style": "diffusion", "range": 50, "spread": 50, "warmth": 0},
    "sharpenRadius": 1, "sharpenDetail": 25, "noiseLuminanceDetail": 50, "noiseColorDetail": 50,
    "calibHue": [0, 0, 0], "calibSat": [0, 0, 0], "superResolution": False,
    "aiDenoise": {"enabled": False, "amount": 50.0},
    **{key: [] for key in CURVES},
    **{key: [0] * 8 for key in MIXER},
    **{key: {"hue": 0, "sat": 0, "lum": 0} for key in WHEELS},
}


def _without_light_depth(settings: dict) -> dict:
    """The settings a recipe file and the native renderer hold. The host effects' own settings (Light Depth, Atmosphere) stay
    with the host (in small files beside the recipe); the recipe carries only the masks they produce."""
    return host_effects.without_host(settings)


def _recipe(settings: dict, image_path: str | None = None) -> dict:
    """The recipe for the native renderer. With ``image_path``, Light Depth's settings in ``settings`` are turned into the
    masks that carry them (built from the photo's cached depth map; they are not kept in the draft)."""
    if image_path is not None:
        settings = host_effects.with_masks(settings, image_path)
    return {"version": bridge.RECIPE_VERSION, "cameraRaw": settings}


class RawEditor(QObject):
    changed = Signal()
    _copied = None  # the look copied with Copy Settings, shared by every editor in the process
    saved = Signal(str, str)
    # Done: the finished picture as the editor shows it (the photo, its image:// address, its size), shown in the photo's place
    # until the background save lands (``saved``) so the edits never seem to vanish meanwhile.
    handedOver = Signal(str, str, int, int)
    recipeSetAside = Signal(str, str)          # unreadable saved settings were set aside on load (the photo, what to say); once
    saveFailed = Signal(str, str)              # a background save of an edit did not land (the photo, why); its draft is kept
    _finished = Signal(int, str, object, str)

    def __init__(self, *, memory=False, parent=None, drafts=None):
        super().__init__(parent)
        self._memory = memory
        # Unsaved drafts are also kept on disk so a crash or a restart does not lose them (never for the demo editor).
        from ..raw_drafts import DraftStore
        self._drafts = None if memory else drafts if drafts is not None else DraftStore()
        # Edits are saved in the background, like a sidecar, when the photo is left or Done is pressed (edit_saves.py)
        self._saves = None
        self._submitted = set()                        # photos this editor handed to the queue whose saves have not all landed
        if not memory:
            from . import edit_saves
            self._saves = edit_saves.instance()
            self._saves.landed.connect(self._landed)
        self._notice = ""
        from ..raw_presets import PresetStore
        self._presets = PresetStore()
        self._path = ""
        self._original = {}
        self._draft = {}
        self._preview = ""
        self._baseline = ""
        self._undo = []
        self._redo = []
        self._sessions = {}
        self._histograms = HistogramService(parent=self)
        self._files = None
        self._error = ""
        self._running = False
        self._saving = False
        self._loaded = False
        self._revision = 0
        self._queued = None
        self._owner = uuid4().hex
        self._proxy = None
        self._proxy_pending = False
        self._published_revision = -1
        self._full_size = [0, 0]
        self._preview_size = [0, 0]
        self._baseline_size = [0, 0]
        self._detail_size = [0, 0]
        self._crop_editing = False
        self._source_view = False
        self._gesture = False
        self._gesture_recorded = False
        self._detail_mode = False
        self._detail = ""
        self._native_job = None
        self._native_action = ""
        self._detail_timer = QTimer(self, singleShot=True, interval=500)
        self._detail_timer.timeout.connect(self._request_detail)
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self._start_queued)
        self._finished.connect(self._arrived)
        from .raw_masks import RawMasks
        self._mask_editor = RawMasks(self)
        from .raw_retouch import RawRetouch
        self._retouch_editor=RawRetouch(self)
        from .raw_red_eye import RawRedEye
        self._red_eye_editor = RawRedEye(self)
        from .raw_light_depth import RawLightDepth
        self._light_depth_editor = RawLightDepth(self)
        from .raw_sky import RawSky
        self._sky_editor = RawSky(self)
        from .raw_atmosphere import RawAtmosphere
        self._atmosphere_editor = RawAtmosphere(self)
        from .raw_bokeh import RawBokeh
        self._bokeh_editor = RawBokeh(self)
        from .raw_erase import RawErase
        self._erase_editor = RawErase(self)
        from .raw_denoise import RawDenoise
        self._denoise_editor = RawDenoise(self)
        from .raw_super_res import RawSuperRes
        self._super_res = RawSuperRes(self)
        from .raw_soft_proof import RawSoftProof
        self._soft_proof = RawSoftProof(self, memory=memory)
        from .raw_sync import RawSync
        self._sync = RawSync(self)
        from .raw_point_color import RawPointColor
        self._point_color = RawPointColor(self)

    pointColor = Property(QObject, lambda self: self._point_color, constant=True)

    denoiseEditor = Property(QObject, lambda self: self._denoise_editor, constant=True)
    superResEditor = Property(QObject, lambda self: self._super_res, constant=True)
    softProof = Property(QObject, lambda self: self._soft_proof, constant=True)

    sync = Property(QObject, lambda self: self._sync, constant=True)

    lightDepthEditor = Property(QObject, lambda self: self._light_depth_editor, constant=True)

    skyEditor = Property(QObject, lambda self: self._sky_editor, constant=True)

    atmosphereEditor = Property(QObject, lambda self: self._atmosphere_editor, constant=True)

    bokehEditor = Property(QObject, lambda self: self._bokeh_editor, constant=True)

    eraseEditor = Property(QObject, lambda self: self._erase_editor, constant=True)

    redEyeEditor = Property(QObject, lambda self: self._red_eye_editor, constant=True)

    retouchEditor=Property(QObject,lambda self:self._retouch_editor,constant=True)

    maskEditor = Property(QObject, lambda self: self._mask_editor, constant=True)

    active = Property(bool, lambda self: bool(self._path), notify=changed)
    busy = Property(bool, lambda self: self._running or self._queued is not None or self._proxy_pending, notify=changed)
    saving = Property(bool, lambda self: self._saving, notify=changed)
    loaded = Property(bool, lambda self: self._loaded, notify=changed)
    dirty = Property(bool, lambda self: self._draft != self._original, notify=changed)

    @Slot()
    def commit(self) -> None:
        """Save the open photo's edit in the background (leaving it, closing the viewer, Done, Save, Export do this)."""
        if self._saves is None or not self._loaded or not self._path or self._saving:
            return
        self._keep_draft()                             # on disk until the save lands
        if self._saves.submit(self._path, self._original, self._draft):
            self._submitted.add(self._path)
            self.changed.emit()

    photoPath = Property(str, lambda self: self._path, notify=changed)
    # A raw (developed from its sensor data: Edit RAW) or a picture (JPEG, TIFF, PNG ...: the same tools on its pixels, as Camera Raw
    # edits one). Tools that need the raw data (AI Denoise, Demosaic, Super Resolution's base) are for raws only.
    isRaw = Property(bool, lambda self: bool(self._path) and Path(self._path).suffix.lower() in bridge.RAW_SUFFIXES, notify=changed)
    autoSaving = Property(bool, lambda self: bool(self._saves is not None and self._path and self._saves.pending(self._path)), notify=changed)

    def _landed(self, path, draft, result, error):
        """A background save finished: the grid and the viewer get the new picture, and what is saved is the photo's new original."""
        if path not in self._submitted:
            return                                     # another editor's save: its drafts and photos are its own
        if not self._saves.pending(path):
            self._submitted.discard(path)
        if error:
            self.saveFailed.emit(path, error)
            self.changed.emit()
            return
        if path == self._path and self._loaded:
            self._original = deepcopy(draft)
            self._keep_draft()                         # removed from the draft store once nothing is unsaved
        elif path in self._sessions:
            session = list(self._sessions[path])
            session[0] = deepcopy(draft)
            self._sessions[path] = tuple(session)
            if session[1] == session[0] and self._drafts is not None:
                self._drafts.discard(path)
        elif self._drafts is not None and not self._saves.pending(path):
            self._drafts.discard(path)                 # closed since: the last save of it landed
        if result:
            self.saved.emit(path, str(result))
        self.changed.emit()

    # -- an AI tool's error and what fixes it (ai_fix.py): the panels show it as a button beside the error ------------------------
    @Slot(str, result=str)
    def fixKind(self, message):
        from .. import ai_fix
        return ai_fix.kind(message, ai_tool=True)

    @Slot(str, result=str)
    def fixLabel(self, message):
        from .. import ai_fix
        return ai_fix.LABELS.get(ai_fix.kind(message, ai_tool=True), "")

    @Slot(result=str)
    def repairApp(self):
        """Put back the app's missing files (Windows Installer); "" once started, else why it could not be."""
        from .. import ai_fix
        return ai_fix.repair_app()

    def unsaved_names(self) -> list[str]:
        """The photos with RAW adjustments that were never saved with Done: the one open now and the ones stepped away from (their drafts are only in memory)."""
        import os

        def unsaved(path, original, draft):
            if draft == original:
                return False
            return self._saves is None or (draft != self._saves.latest(path) and draft != self._saves.saved(path))

        paths = [path for path, session in self._sessions.items() if unsaved(path, session[0], session[1])]
        if self._path and self._loaded and unsaved(self._path, self._original, self._draft):
            paths.append(self._path)
        return [os.path.basename(path) for path in paths]

    unsavedCount = Property(int, lambda self: len(self.unsaved_names()), notify=changed)

    @Slot(result=str)
    def unsavedSummary(self) -> str:
        names = self.unsaved_names()
        return ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    @Property("QVariantMap", notify=changed)
    def values(self):
        values = deepcopy(DEFAULTS)
        values.update(deepcopy(self._draft))
        for key in ("glow", "hostCrop", *WHEELS):      # a crop saved before it could be turned has no turn in it
            values[key] = {**DEFAULTS[key], **self._draft.get(key, {})}
        return values

    def _cropped_url(self, path, clip):
        """The picture's address with how it is framed for display. The crop tool shows the whole frame, turned by quarter-turns
        and mirrors so the rectangle can be drawn over it; the tools that draw on the picture as the camera recorded it (masks,
        retouching, red eye) show it cropped and not turned; everywhere else it is the finished picture."""
        url = _image_url(path)
        if not url.startswith("image://"):
            return url                       # a file preview (no preview helper): QML clips it; a turn is only in what is saved
        from ..raw_crop import framed
        crop = self.values["hostCrop"]
        parts = []
        if not self._source_view or self._crop_editing:
            from ..raw_perspective import query as perspective_query

            warp = perspective_query({**DEFAULT_CROP, **crop})      # masks and retouching are drawn on the picture as recorded
            if warp:
                parts.append(warp)
        if not self._crop_editing and clip["w"] > 0 and framed(crop):
            parts.append("crop=" + ",".join(str(clip[key]) for key in ("x", "y", "w", "h")))
        if not self._source_view or self._crop_editing:
            turn = raw_preview.turn_query(crop, quarter_only=self._crop_editing)
            if turn:
                parts.append(turn)
        proof = self._soft_proof.query() if hasattr(self, "_soft_proof") else ""
        if proof:
            parts.append(proof)            # the picture on screen only: frames read by the tools are not proofed
        return url + ("?" + "&".join(parts) if parts else "")

    preview = Property(str, lambda self: self._cropped_url(self._detail if self._detail_mode and self._detail else self._preview, self.previewClip), notify=changed)
    baseline = Property(str, lambda self: self._cropped_url(self._baseline, self.baselineClip), notify=changed)
    detailPending = Property(bool, lambda self: self._detail_mode and not self._detail, notify=changed)
    def _display_size(self):
        from ..raw_crop import turned_size
        crop = self.values["hostCrop"]
        if min(self._full_size) <= 0:
            return self._full_size
        if self._crop_editing:
            return list(turned_size(crop, *self._full_size, quarter_only=True))
        size = crop_bounds(crop, *self._full_size)[2:]
        size = list(size) if self._source_view else list(turned_size(crop, *size))
        # Super Resolution makes the output twice the size: 100% is then one upscaled pixel per screen pixel (raw_super_res.py)
        return [v * 2 for v in size] if self._draft.get("superResolution") is True else size

    fullWidth = Property(int, lambda self: self._display_size()[0], notify=changed)
    fullHeight = Property(int, lambda self: self._display_size()[1], notify=changed)
    originalWidth = Property(int, lambda self: self._full_size[0], notify=changed)
    originalHeight = Property(int, lambda self: self._full_size[1], notify=changed)
    cropEditing = Property(bool, lambda self: self._crop_editing, notify=changed)
    sourceView = Property(bool, lambda self: self._source_view, notify=changed)

    @Slot(bool)
    def setSourceView(self, enabled):
        """Show the picture as the camera recorded it (cropped, not turned): the masks, retouching and red eye are drawn on that."""
        if self._source_view != enabled:
            self._source_view = enabled
            self.changed.emit()

    def source_point(self, x, y):
        """Where a point of the picture as shown (0..1) is in the developed picture as recorded."""
        from ..raw_crop import view_to_frame, view_to_source
        crop = self.values["hostCrop"]
        if self._crop_editing:
            from ..raw_perspective import warped_to_recorded

            u, v = view_to_frame(x, y, crop, quarter_only=True)
            shape = tuple(self._full_size) if min(self._full_size) > 0 else (1.0, 1.0)
            return warped_to_recorded(u, v, {**DEFAULT_CROP, **crop}, shape)
        if self._source_view:
            return crop["x"] + x * crop["w"], crop["y"] + y * crop["h"]
        return view_to_source(x, y, crop, shape=tuple(self._full_size) if min(self._full_size) > 0 else (1.0, 1.0))

    # -- the crop as the crop tool shows it: over the whole frame after quarter-turns and mirrors ---------------------------------
    @Property("QVariantMap", notify=changed)
    def cropView(self):
        from ..raw_crop import rect_to_view
        x, y, w, h = rect_to_view(self.values["hostCrop"])
        return {"x": x, "y": y, "w": w, "h": h}

    cropAngle = Property(float, lambda self: __import__("image_triage.raw_crop", fromlist=["shown_angle"]).shown_angle(self.values["hostCrop"]), notify=changed)
    cropTurned = Property(bool, lambda self: __import__("image_triage.raw_crop", fromlist=["turned"]).turned(self.values["hostCrop"]), notify=changed)

    @Slot(float, float, float, float)
    def setCropView(self, x, y, width, height):
        from ..raw_crop import rect_from_view
        if all(math.isfinite(v) for v in (x, y, width, height)):
            self.setCrop(*(min(1.0, max(0.0, v)) for v in rect_from_view(x, y, width, height, self.values["hostCrop"])))

    @Slot(int)
    def rotateCrop(self, quarter_turns):
        """Turn what is shown by quarter-turns, clockwise for a positive number."""
        from ..raw_crop import rotated
        self._set_values({"hostCrop": rotated(self.values["hostCrop"], int(quarter_turns))}, "Rotate", render=False)

    @Slot(bool)
    def flipCrop(self, horizontal):
        from ..raw_crop import flipped
        self._set_values({"hostCrop": flipped(self.values["hostCrop"], bool(horizontal))}, "Flip horizontal" if horizontal else "Flip vertical", render=False)

    @Slot(float)
    def setCropAngle(self, angle):
        """Straighten: ``angle`` is in degrees as the person sees the picture turn (clockwise positive)."""
        from ..raw_crop import MAX_ANGLE
        if not math.isfinite(angle):
            return
        crop = self.values["hostCrop"]
        angle = max(-MAX_ANGLE, min(MAX_ANGLE, round(angle, 2)))
        crop["angle"] = -angle if crop["flipH"] != crop["flipV"] else angle
        self._set_values({"hostCrop": crop}, "Straighten", render=False)

    @Slot(str, float)
    def setTransform(self, key, value):
        """Crop > Transform: one of the perspective sliders (``raw_perspective``), or Constrain crop (1 or 0)."""
        from ..raw_perspective import KEYS, RANGES
        if key not in KEYS or not math.isfinite(value):
            return
        crop = self.values["hostCrop"]
        if key in ("vertical", "horizontal") and crop.get("upright", "off") != "guided":
            crop.pop("upright", None)            # moved by hand: no longer what an Upright mode found
        if key == "constrain":
            crop["constrain"] = bool(value)
        else:
            low, high = RANGES[key]
            crop[key] = round(max(low, min(high, float(value))), 1)
        self._set_values({"hostCrop": crop}, "Transform", render=False)

    def _shown_crop_angle(self, crop, angle):
        """A straighten angle as seen (clockwise) as it is stored (a single mirror reverses it), as ``setCropAngle`` does."""
        from ..raw_crop import MAX_ANGLE
        angle = max(-MAX_ANGLE, min(MAX_ANGLE, round(angle, 2)))
        return -angle if crop["flipH"] != crop["flipV"] else angle

    def _solved(self, crop, lines, mode):
        from ..raw_upright import solve
        width, height = self._full_size if min(self._full_size) > 0 else (3, 2)
        shown = (height, width) if crop.get("rotate") in (90, 270) else (width, height)
        result = solve(lines, *shown, mode)
        crop.update(vertical=result["vertical"], horizontal=result["horizontal"], upright=mode)
        crop["angle"] = self._shown_crop_angle(crop, result["angle"])
        return crop

    @Slot(str)
    def setUpright(self, mode):
        """Crop > Transform > Upright: Off, Auto, Level, Vertical or Full solve the sliders from the lines found in the photo; Guided
        from the guides drawn on it."""
        from ..raw_upright import MODES, auto_lines
        from ..raw_crop import turn
        if mode not in MODES or not self._loaded or self._saving:
            return
        crop = self.values["hostCrop"]
        if mode == "off":
            from ..raw_perspective import KEYS
            crop = {k: v for k, v in crop.items() if k not in ("upright", "guides", "vertical", "horizontal")}
            self._set_values({"hostCrop": crop}, "Upright off", render=False)
            return
        if mode == "guided":
            crop["upright"] = "guided"
            crop = self._solved(crop, crop.get("guides", []), "guided") if crop.get("guides") else crop
            self._set_values({"hostCrop": crop}, "Guided upright", render=False)
            return
        from PySide6.QtGui import QImage
        image = raw_preview.frame_image(self._preview) if self._preview.startswith("image://") else QImage(self._preview)
        if image.isNull():
            return
        shown = turn(image, {**crop, "angle": 0.0}, quarter_only=True)
        lines = auto_lines(shown, mode)
        crop.pop("guides", None)
        self._set_values({"hostCrop": self._solved(crop, lines, mode)}, f"Upright {mode}", render=False)

    @Property("QVariantList", notify=changed)
    def uprightGuides(self):
        """The guides as the crop tool shows them (over the warped frame, 0..1)."""
        from ..raw_perspective import apply, shown_homography
        crop = {**DEFAULT_CROP, **self.values["hostCrop"]}
        width, height = self._full_size if min(self._full_size) > 0 else (3, 2)
        h, sw, sh = shown_homography(crop, width, height)
        shown = []
        for x1, y1, x2, y2 in crop.get("guides", []):
            a, b = apply(h, x1 * sw, y1 * sh), apply(h, x2 * sw, y2 * sh)
            shown.append([a[0] / sw, a[1] / sh, b[0] / sw, b[1] / sh])
        return shown

    @Slot(float, float, float, float)
    def addGuide(self, x1, y1, x2, y2):
        """A guide drawn in the crop tool (0..1 over the warped frame) on a line that should be vertical or horizontal."""
        from ..raw_perspective import _inv, apply, shown_homography
        from ..raw_upright import MAX_GUIDES
        if not all(math.isfinite(v) for v in (x1, y1, x2, y2)) or math.hypot(x2 - x1, y2 - y1) < 0.02:
            return
        crop = self.values["hostCrop"]
        width, height = self._full_size if min(self._full_size) > 0 else (3, 2)
        h, sw, sh = shown_homography({**DEFAULT_CROP, **crop}, width, height)
        back = _inv(h)
        a, b = apply(back, x1 * sw, y1 * sh), apply(back, x2 * sw, y2 * sh)
        guides = list(crop.get("guides", []))[-(MAX_GUIDES - 1):] + [[round(a[0] / sw, 5), round(a[1] / sh, 5), round(b[0] / sw, 5), round(b[1] / sh, 5)]]
        crop["guides"] = guides
        self._set_values({"hostCrop": self._solved(crop, guides, "guided")}, "Upright guide", render=False)

    @Slot()
    def clearGuides(self):
        crop = {k: v for k, v in self.values["hostCrop"].items() if k not in ("guides", "vertical", "horizontal")}
        crop["upright"] = "guided"
        self._set_values({"hostCrop": crop}, "Clear guides", render=False)

    @Slot()
    def resetTransform(self):
        from ..raw_perspective import KEYS, EXTRA
        crop = {k: v for k, v in self.values["hostCrop"].items() if k not in KEYS and k not in EXTRA}
        self._set_values({"hostCrop": crop}, "Reset transform", render=False)

    @Property("QVariantMap", notify=changed)
    def previewClip(self):
        size = self._detail_size if self._detail_mode and self._detail else self._preview_size
        if self._crop_editing or min(size) <= 0:
            return {"x": 0, "y": 0, "w": 0, "h": 0}
        x, y, w, h = crop_bounds(self.values["hostCrop"], *size)
        return {"x": x, "y": y, "w": w, "h": h}
    @Property("QVariantMap", notify=changed)
    def baselineClip(self):
        if self._crop_editing or min(self._baseline_size) <= 0:
            return {"x": 0, "y": 0, "w": 0, "h": 0}
        x, y, w, h = crop_bounds(self.values["hostCrop"], *self._baseline_size)
        return {"x": x, "y": y, "w": w, "h": h}

    histogramPath = Property(str, lambda self: self._preview or self._path, notify=changed)
    baselinePath = Property(str, lambda self: self._baseline or self._path, notify=changed)
    histograms = Property(QObject, lambda self: self._histograms, constant=True)
    history = Property("QStringList", lambda self: [label for label, _ in reversed(self._undo)], notify=changed)
    canUndo = Property(bool, lambda self: bool(self._undo) and not self._saving, notify=changed)
    canRedo = Property(bool, lambda self: bool(self._redo) and not self._saving, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    # Something the person should know that is not a failure, such as unsaved edits brought back from an earlier session.
    notice = Property(str, lambda self: self._notice, notify=changed)

    def _keep_draft(self):
        """Put the open photo's draft on disk, or remove it once there is nothing unsaved."""
        if self._drafts is None or not self._path or not self._loaded:
            return
        if self._draft == self._original:
            self._drafts.discard(self._path)
        else:
            self._drafts.put(self._path, deepcopy(self._original), deepcopy(self._draft))

    @Slot(str)
    def open(self, path: str) -> None:
        if self._saving or path == self._path or not path:
            return
        self._stop_detail()
        self._proxy_pending = False
        self._detail_size = [0, 0]
        if hasattr(self, "_proxy_request"):
            del self._proxy_request
        self._gesture = self._gesture_recorded = False
        self.commit()                                  # the photo being left is saved in the background
        if self._path and self._loaded:
            self._sessions[self._path] = (
                self._original, self._draft, self._preview, self._baseline,
                self._undo, self._redo, self._full_size, self._preview_size, self._baseline_size,
            )
        self._full_size = [0, 0]
        self._preview_size = self._baseline_size = [0, 0]
        self._revision += 1
        self._timer.stop()
        self._queued = None
        self._error = self._notice = ""
        self._path = path
        self._original = {}
        self._draft = {}
        self._preview = self._baseline = ""
        self._undo = []
        self._redo = []
        self._loaded = self._memory
        restored = self._sessions.pop(path, None)
        if restored is not None:
            (self._original, self._draft, self._preview, self._baseline,
             self._undo, self._redo, self._full_size, self._preview_size, self._baseline_size) = restored
            if self._preview.startswith("image://") and not raw_preview.has_frame(self._preview):
                self._preview = ""
            if self._baseline.startswith("image://") and not raw_preview.has_frame(self._baseline):
                self._baseline = ""
            self._loaded = True
            if not self._memory and (self.dirty or raw_preview.executable()):
                self._enqueue("preview")
        elif Path(path).suffix.lower() not in EDITOR_SUFFIXES:
            self._loaded = False
            self._error = "This kind of file cannot be edited here."
        elif not self._memory:
            self._enqueue("load", self._drafts)
        self.changed.emit()

    # --- auto tone / auto white balance: a toggle. Press once to apply it, press again to take it off. ---
    _AUTO = {"tone": ("Auto tone", "No change needed."), "wb": ("Auto white balance", "No clear colour cast found.")}

    def _auto_active(self, what: str) -> bool:
        record = self.__dict__.get("_auto_records", {}).get(what)
        if not record or record["path"] != self._path:
            return False
        return all(self._draft.get(key, DEFAULTS[key]) == value for key, value in record["applied"].items())

    autoToneOn = Property(bool, lambda self: self._auto_active("tone"), notify=changed)
    autoWbOn = Property(bool, lambda self: self._auto_active("wb"), notify=changed)
    autoStrength = Property(float, lambda self: self.__dict__.get("_auto_strength", 100.0), notify=changed)

    @staticmethod
    def _auto_blend(full: dict, previous: dict, strength: float) -> dict:
        """The slider values at ``strength`` (0..1) between the values before Auto and what Auto asked for."""
        out = {}
        for key, target in full.items():
            value = previous[key] + (target - previous[key]) * strength
            out[key] = round(value, 2) if key == "exposure" else round(value)
        return out

    @Slot(str)
    def toggleAuto(self, what: str) -> None:
        """Apply auto tone ("tone") or auto white balance ("wb") from the picture on screen; when it is already applied, take it
        off again. One undo step either way."""
        if not self._loaded or self._saving or what not in self._AUTO:
            return
        records = self.__dict__.setdefault("_auto_records", {})
        label, nothing = self._AUTO[what]
        if self._auto_active(what):
            record = records.pop(what)
            self._notice = ""
            self._set_values(record["previous"], label + " off")
            self.changed.emit()
            return
        from .. import auto_tone
        from . import raw_preview
        from .raw_erase import image_to_array

        image = raw_preview.frame_image(self._preview) if self._preview else None
        if image is None or image.isNull():
            self._notice = "Wait for the preview to finish, then try again."
            self.changed.emit()
            return
        current = {key: self._draft.get(key, DEFAULTS[key]) for key in (*auto_tone.TONE_KEYS, *auto_tone.WB_KEYS)}
        compute = auto_tone.tone if what == "tone" else auto_tone.white_balance
        full = compute(image_to_array(image), current, 1.0)
        if not full:
            records.pop(what, None)
            self._notice = nothing
            self.changed.emit()
            return
        previous = {key: current[key] for key in full}
        applied = self._auto_blend(full, previous, self.__dict__.get("_auto_strength", 100.0) / 100)
        records[what] = {"path": self._path, "full": full, "previous": previous, "applied": applied}
        self._notice = ""
        self._set_values(applied, label)
        records[what]["applied"] = {key: self._draft.get(key, DEFAULTS[key]) for key in applied}
        self.changed.emit()

    @Slot(float)
    def setAutoStrength(self, value: float) -> None:
        """How much of what Auto asked for is applied (a percentage). Takes effect at once on whatever Auto is applied."""
        if not math.isfinite(value):
            return
        self._auto_strength = max(0.0, min(100.0, value))
        for what, record in list(self.__dict__.get("_auto_records", {}).items()):
            if self._auto_active(what):
                applied = self._auto_blend(record["full"], record["previous"], self._auto_strength / 100)
                # the record must already say what is about to be applied: the panel reads "is Auto on" while the draft changes,
                # and a record that is a step behind switches Auto off for a moment, which drops the slider being dragged
                record["applied"] = applied
                self._set_values(applied, self._AUTO[what][0] + " strength")
                record["applied"] = {key: self._draft.get(key, DEFAULTS[key]) for key in applied}
        self.changed.emit()

    @Slot(str, float)
    def adjust(self, key: str, value: float) -> None:
        if not self._loaded or self._saving or key not in FIELDS or not math.isfinite(value):
            return
        low, high = FIELDS[key]
        value = max(low, min(high, value))
        self._set_value(key, value, key.capitalize())

    def _set_value(self, key, value, label):
        self._set_values({key: value}, label)

    def _set_values(self, updates, label, render=True):
        if not self._loaded or self._saving:
            return
        draft = dict(self._draft)
        for key, value in updates.items():
            if value == draft.get(key, DEFAULTS[key]):
                continue
            if value == DEFAULTS[key] and key not in self._original:
                draft.pop(key, None)
            else:
                draft[key] = deepcopy(value)
        if draft == self._draft:
            return
        if not self._gesture or not self._gesture_recorded:
            self._undo.append((label, dict(self._draft)))
            self._gesture_recorded = self._gesture
        self._redo.clear()
        self._draft = draft
        if render:
            self._draft_changed()
        else:
            self._keep_draft()
            self.changed.emit()

    @Slot(bool)
    def setCropEditing(self, enabled):
        if self._crop_editing != enabled:
            self._crop_editing = enabled
            self.changed.emit()

    @Slot(float, float, float, float)
    def setCrop(self, x, y, width, height):
        crop = {**self.values["hostCrop"], "x": x, "y": y, "w": width, "h": height}
        if crop_valid(crop):
            self._set_values({"hostCrop": crop}, "Crop", render=False)

    @Slot(str)
    def setCropRatio(self, ratio):
        if ratio not in RATIOS:
            return
        crop = {**self.values["hostCrop"], "ratio": ratio}
        if ratio != "Free":
            # The ratio is of the picture as shown, so it is worked out there (a quarter-turn swaps width and height) and put back.
            from ..raw_crop import rect_from_view, rect_to_view, turned_size
            shown = turned_size(crop, *self._full_size, quarter_only=True) if self._full_size[0] else (1, 1)
            aspect = shown[0] / max(1, shown[1])
            desired = aspect if ratio == "Original" else float(ratio.split(":")[0]) / float(ratio.split(":")[1])
            normalized = desired / aspect
            x, y, width, height = rect_to_view(crop)
            if width / height > normalized:
                narrower = height * normalized
                x, width = x + (width - narrower) / 2, narrower
            else:
                shorter = width / normalized
                y, height = y + (height - shorter) / 2, shorter
            left, top, wide, high = rect_from_view(x, y, width, height, crop)
            crop.update(x=left, y=top, w=wide, h=high)
        self._set_values({"hostCrop": crop}, "Crop ratio", render=False)

    @Slot()
    def resetCrop(self):
        from ..raw_perspective import KEYS
        kept = {k: v for k, v in self.values["hostCrop"].items() if k in KEYS}      # Reset transform has its own button
        self._set_values({"hostCrop": {**DEFAULT_CROP, **kept}}, "Reset crop", render=False)

    @Slot()
    def beginGesture(self):
        if not self._gesture:
            self._gesture = True
            self._gesture_recorded = False
            self._mask_editor.changed.emit()

    @Slot()
    def endGesture(self):
        self._light_depth_editor.flush()           # a slider's last position is committed inside its gesture, as one undo step
        self._atmosphere_editor.flush()
        self._bokeh_editor.flush()
        self._denoise_editor.flush()
        changed = self._gesture_recorded
        self._gesture = self._gesture_recorded = False
        self._mask_editor.changed.emit()
        if changed and not self._memory and raw_preview.executable():
            self._enqueue("preview")
        if self._detail_mode:
            self._detail_timer.start()

    @Slot(bool)
    def setDetailMode(self, enabled):
        if self._detail_mode == enabled:
            return
        self._detail_mode = enabled
        if enabled and self._loaded and not self._memory:
            self._detail_timer.start()
        elif not enabled:
            self._stop_detail()
        self.changed.emit()

    def _stop_detail(self):
        self._detail_timer.stop()
        if self._native_action == "detail" and self._native_job:
            self._native_job.cancel()
        self._discard_preview(self._detail)
        self._detail = ""

    def _request_detail(self):
        if not self._memory and self._detail_mode and self._loaded and not self._saving and not self._gesture:
            self._enqueue("detail")
            self.changed.emit()

    @Slot()
    def asShotWhiteBalance(self):
        self._set_values({"temperature": 0, "tint": 0}, "White balance: As Shot")

    @Slot(float, float)
    def pickWhiteBalance(self, x, y):
        if not self._loaded or self._saving or not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            return
        x, y = (min(1.0, max(0.0, value)) for value in self.source_point(x, y))
        self._enqueue("whiteBalance", (x, y))
        self.changed.emit()

    @Slot(str, float)
    def setGlow(self, component, value):
        if component not in ("amount", "range", "spread", "warmth") or not math.isfinite(value):
            return
        settings = dict(self.values["glow"])
        settings[component] = max(-100 if component == "warmth" else 0, min(100, value))
        self._set_value("glow", settings, "Glow")

    @Slot(str, str)
    def setEffectStyle(self, effect, style):
        if effect == "vignette" and style in ("highlightPriority", "colorPriority", "paintOverlay"):
            self._set_value("vignetteStyle", style, "Vignette style")
        elif effect == "glow" and style in ("diffusion", "bloom", "halation"):
            settings = dict(self.values["glow"])
            settings["style"] = style
            self._set_value("glow", settings, "Glow style")

    @Slot(int, str, float)
    def setCalibration(self, channel, component, value):
        if channel not in range(3) or component not in ("calibHue", "calibSat") or not math.isfinite(value):
            return
        values = list(self._draft.get(component, DEFAULTS[component]))
        values[channel] = max(-100, min(100, value))
        self._set_value(component, values, "Calibration")

    @Slot(bool)
    def setSuperResolution(self, enabled):
        self._set_value("superResolution", enabled, "Super Resolution")

    @Slot(str, "QVariantList")
    def setCurve(self, channel, points):
        if channel not in CURVES:
            return
        try:
            values = [[float(x), float(y)] for x, y in points]
        except (TypeError, ValueError, OverflowError):
            return
        if values and not 2 <= len(values) <= 16:
            return
        if any(not math.isfinite(v) or not 0 <= v <= 255 for point in values for v in point):
            return
        if any(right[0] - left[0] < 1 for left, right in zip(values, values[1:])):
            return
        if values == [[0, 0], [255, 255]] and not self._draft.get(channel):
            return
        self._set_value(channel, values, "Tone curve")

    @Slot(int, str, float)
    def setMixer(self, channel, component, value):
        if component not in MIXER or channel not in range(-1, 8) or not math.isfinite(value):
            return
        values = list(self._draft.get(component, DEFAULTS[component]))
        value = max(-100, min(100, value))
        if channel == -1:
            values = [value] * 8
        else:
            values[channel] = value
        self._set_value(component, values, "Color mixer")

    @Slot(str, float, float, float)
    def setGrading(self, wheel, hue, saturation, luminance):
        if wheel not in WHEELS or not all(math.isfinite(v) for v in (hue, saturation, luminance)):
            return
        value = {"hue": hue % 360, "sat": max(0, min(100, saturation)), "lum": max(-100, min(100, luminance))}
        self._set_value(wheel, value, "Color grading")

    # --- moving a look between photos: copy/paste and saved presets -------------------------------------------------

    canPaste = Property(bool, lambda self: RawEditor._copied is not None and self._loaded and not self._saving, notify=changed)
    presets = Property("QStringList", lambda self: [preset["name"] for preset in self._presets.presets()], notify=changed)

    def _look(self):
        from ..raw_presets import transferable
        return deepcopy(transferable(self._draft))

    def _apply_look(self, look, label):
        """Replace this photo's look with ``look`` as one undo step. Masks, retouching, red eye, crop, Light Depth and
        Super Resolution are this photo's own and stay as they are."""
        from ..raw_presets import usable, with_look
        if not self._loaded or self._saving or not usable(look):
            return False
        draft = with_look(self._draft, look, self._original, DEFAULTS)
        if draft == self._draft:
            return True
        self._undo.append((label, dict(self._draft)))
        self._redo.clear()
        self._draft = draft
        self._draft_changed()
        return True

    @Slot()
    def copySettings(self) -> None:
        if self._loaded:
            RawEditor._copied = self._look()
            self._notice = "Settings copied. Paste them on another photo."
            self.changed.emit()

    @Slot()
    def pasteSettings(self) -> None:
        if RawEditor._copied is not None and self._apply_look(RawEditor._copied, "Paste settings"):
            self._notice = "Settings pasted. Masks, retouching and crop were left as they were."
            self.changed.emit()

    @Slot(str)
    def savePreset(self, name: str) -> None:
        if not self._loaded:
            return
        self._error = self._presets.save(name, self._look())
        if not self._error:
            self._notice = "Preset saved."
        self.changed.emit()

    @Slot(str)
    def applyPreset(self, name: str) -> None:
        look = self._presets.get(name)
        if look is not None and self._apply_look(look, "Preset: " + name[:40]):
            self._notice = ""
            self.changed.emit()

    @Slot(str)
    def deletePreset(self, name: str) -> None:
        self._error = self._presets.delete(name)
        self.changed.emit()

    @Slot()
    def undo(self) -> None:
        self._step_history(self._undo, self._redo)

    @Slot()
    def redo(self) -> None:
        self._step_history(self._redo, self._undo)

    @Slot(int)
    def restoreHistory(self, index: int) -> None:
        if self._saving or index < 0 or index >= len(self._undo):
            return
        for _ in range(index + 1):
            label, draft = self._undo.pop()
            self._redo.append((label, self._draft))
            self._draft = draft
        self._draft_changed()

    @Slot()
    def reset(self) -> None:
        if not self._loaded or self._saving:
            return
        draft = dict(self._draft)
        for key, default in DEFAULTS.items():
            # Global Reset keeps authored masks, as it did before the Mask tab.
            if key in ("masks","retouch","redEye","lightDepth","atmosphere","bokeh","sky"):
                continue
            if key in self._original:
                draft[key] = deepcopy(default)
            else:
                draft.pop(key, None)
        if draft == self._draft:
            return
        self._undo.append(("Reset", dict(self._draft)))
        self._redo.clear()
        self._draft = draft
        self._draft_changed()

    def _step_history(self, source, destination):
        if self._saving or not source:
            return
        label, draft = source.pop()
        destination.append((label, self._draft))
        self._draft = draft
        self._draft_changed()

    def _draft_changed(self):
        self._error = ""
        self._keep_draft()
        self._stop_detail()
        if not self._memory:
            self._enqueue("preview")
        self.changed.emit()
        if self._detail_mode and not self._gesture:
            self._detail_timer.start()

    @Slot()
    def cancel(self) -> None:
        """Leave the photo and drop what was not saved, including its stored draft."""
        self._close(True)

    def _close(self, forget: bool) -> None:
        if self._saving:
            return
        if forget and self._drafts is not None and self._path:
            self._drafts.discard(self._path)
        self._notice = ""
        self._stop_detail()
        if self._native_job:
            self._native_job.cancel()
        if self._proxy:
            self._proxy.close()
            self._proxy.deleteLater()
            self._proxy = None
        self._proxy_pending = False
        self._revision += 1
        self._timer.stop()
        self._queued = None
        self._discard_preview(self._preview)
        self._path = self._preview = self._error = ""
        self._baseline = ""
        self._undo.clear()
        self._redo.clear()
        self._draft = self._original = {}
        self._loaded = False
        self._gesture = self._gesture_recorded = False
        raw_preview.clear_frames(self._owner)
        if not self._running and not self._sessions:
            self._clear_files()
        self.changed.emit()

    @Slot()
    def done(self) -> None:
        if not self._loaded or self._saving:
            return
        if self._draft == self._original or self._memory:
            self.cancel()
            return
        if self._saves is not None:
            self._hand_over()
            self.commit()                              # saved in the background; the draft stays on disk until it lands
            self._close(False)
            return
        self._saving = True
        self._stop_detail()
        self._proxy_pending = False
        if self._proxy:
            self._proxy.close()
            self._proxy.deleteLater()
            self._proxy = None
        self._error = ""
        self._enqueue("save")
        self.changed.emit()

    def _hand_over(self):
        """Give the viewer the finished picture as shown now (``handedOver``), kept apart from the editor's own frames."""
        from PySide6.QtCore import Qt

        url = self.property("preview")
        if not url.startswith("image://rawpreview/") or not self._path:
            return
        image = raw_preview.displayed_frame(url, proof=False)
        if image.isNull():
            return
        if max(image.width(), image.height()) > 4096:          # the full-resolution detail picture: a screen's worth is enough
            image = image.scaled(4096, 4096, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._handovers = getattr(self, "_handovers", 0) + 1
        shown = raw_preview.put_thumbnail(f"done/{self._owner}/{self._handovers}", image)
        width, height = self.property("fullWidth"), self.property("fullHeight")
        if min(width, height) <= 0:
            width, height = image.width(), image.height()
        self.handedOver.emit(self._path, shown, width, height)

    def _clear_files(self):
        if self._files is not None:
            self._files.cleanup()
            self._files = None

    def has_unsaved(self, path: str) -> bool:
        """Whether ``path`` has edits never saved with Done: open now, stepped away from, or kept from an earlier session."""
        if path == self._path and self._loaded:
            return self._draft != self._original
        session = self._sessions.get(path)
        if session is not None and session[1] != session[0]:
            return True
        return self._drafts is not None and self._drafts.file_for(path).exists()

    def shutdown(self):
        """The application is closing. Unsaved drafts stay on disk and come back when their photos are edited again."""
        self._super_res.shutdown()                     # stops a base being made and the worker that develops the view
        self._sync.cancel()
        self._sessions.clear()
        self._close(False)
        if self._drafts is not None:
            self._drafts.close()

    def _enqueue(self, action: str, sample=None) -> None:
        if self._saving and action != "save":
            # Nothing may start while a save is running: it would move the revision on, the save's result would be taken
            # for a stale one, and the editor would stay "saving" (refusing open, cancel, done and every slider) for good.
            return
        if action != "detail":
            self._revision += 1
        if action not in ("preview", "detail"):
            self._proxy_pending = False
            if hasattr(self, "_proxy_request"):
                del self._proxy_request
        if action in ("preview", "whiteBalance", "maskSample") and raw_preview.executable():
            self._proxy_pending = True
            # A bounded dispatch interval during dragging, not a trailing-edge
            # debounce that waits until the person stops moving the pointer.
            self._proxy_request = (self._revision, self._path, deepcopy(self._original), deepcopy(self._draft),
                                   "draft" if self._gesture else "settled", sample)
            if not self._timer.isActive():
                self._timer.start()
            return
        self._queued = (self._revision, action, self._path, dict(self._original), dict(self._draft), sample)
        if action == "preview":
            self._timer.start()
        else:
            self._timer.stop()
            self._start_queued()

    def _start_queued(self) -> None:
        if self._proxy_pending and hasattr(self, "_proxy_request"):
            if self._proxy is None:
                self._proxy = raw_preview.PreviewWorker(raw_preview.executable(), self)
                self._proxy.completed.connect(self._proxy_arrived)
                self._proxy.sampled.connect(self._proxy_sampled)
                self._proxy.maskSampled.connect(self._mask_sampled)
                self._proxy.sourceReady.connect(self._source_arrived)
                self._proxy.coverageReady.connect(self._coverage_arrived)
                self._proxy.failed.connect(self._proxy_failed)
            request = self._proxy_request
            del self._proxy_request
            if request[0] == self._revision and not self._saving:
                coverage=self._mask_editor.current
                if any(p.get("shape",{}).get("type") in ("colorRange","luminanceRange") for p in [coverage,*coverage.get("parts",[])]):
                    self._proxy.submit(*request,coverage=coverage)
                else:self._proxy.submit(*request)
        if self._running or self._queued is None:
            return
        revision, action, path, original, draft, sample = self._queued
        self._queued = None
        if revision != self._revision:
            return
        if action in ("preview", "whiteBalance", "detail") and self._files is None:
            # A preview picture can still be held open by Windows (or the editor) when the app closes: leaving that one small temporary
            # file behind is better than a traceback at exit.
            self._files = tempfile.TemporaryDirectory(prefix="image-triage-raw-", ignore_cleanup_errors=True)
        directory = self._files.name if self._files else ""
        self._running = True
        job = self._native_job = _NativeJob()
        self._native_action = action
        self.changed.emit()

        def work():
            _native_context.job = job
            try:
                result = self._work(action, path, original, draft, directory, sample)
                if action == "detail":
                    # Decode on this worker, then publish pixels. QML must not
                    # keep a Windows file handle open while a new edit deletes
                    # the old full-resolution temporary PNG.
                    from PySide6.QtGui import QImageReader
                    try:
                        reader = QImageReader(result)
                        image = reader.read()
                        del reader
                        if image.isNull():
                            raise ValueError("The full-resolution preview could not be decoded.")
                    finally:
                        Path(result).unlink(missing_ok=True)
                    # PhotoCraft develops the photo; Atmosphere and Bokeh are the host's, laid over it as on the preview worker's
                    # pictures. Without this, zooming in swapped the fogged picture for one without fog once the detail arrived.
                    image = atmosphere.fogged_frame(({}, image), path, draft.get("atmosphere"))[1]
                    image = bokeh.bokeh_frame(({}, image), path, draft.get("bokeh"))[1]
                    result = image
                self._finished.emit(revision, action, result, "")
            except Exception as error:
                self._finished.emit(revision, action, None, str(error) or "The editor could not complete the RAW request.")

        threading.Thread(target=work, name="qml-raw-editor", daemon=True).start()

    def _proxy_arrived(self, revision, result):
        if (not self._loaded or self._saving or revision < self._published_revision
                or (revision != self._revision and not self._gesture)):
            return
        (header, image), (base_header, base_image), elapsed, path = result
        if path != self._path:
            return
        self._published_revision = revision
        self._full_size = header["fullSize"]
        self._preview_size = [image.width(), image.height()]
        self._baseline_size = [base_image.width(), base_image.height()]
        self._proxy_pending = revision != self._revision
        self._error = ""
        self._discard_preview(self._preview)
        self._preview = raw_preview.put_frame(f"{self._owner}/{revision}/after", image)
        self._baseline = raw_preview.put_frame(f"{self._owner}/{revision}/before", base_image)
        self._histograms.publish_preview(self._preview, raw_preview.histogram(header))
        self._histograms.publish_preview(self._baseline, raw_preview.histogram(base_header))
        self.changed.emit()
        if self._detail_mode and not self._detail and not self._gesture:
            self._detail_timer.start()

    def _proxy_failed(self, revision, error):
        if revision == self._revision:
            self._proxy_pending = False
            self._error = error
            self.changed.emit()

    def _proxy_sampled(self, revision, result):
        if revision != self._revision or not self._loaded or self._saving:
            return
        self._proxy_pending = False
        image, (x, y) = result
        from PySide6.QtCore import QRect
        px, py = min(image.width()-1, int(x*image.width())), min(image.height()-1, int(y*image.height()))
        try:
            values = _white_balance_from_image(image.copy(QRect(px-2,py-2,5,5).intersected(image.rect())))
            self._set_values(values, "White balance sample")
        except ValueError as error:
            self._error = str(error)
        self.changed.emit()

    def _mask_sampled(self, revision, result):
        if revision != self._revision or not self._loaded or self._saving:
            return
        self._proxy_pending = False
        try:
            target = self._point_color if result[1].get("kind") == "pointColor" else self._mask_editor
            target.applySample(*result)
        except (ValueError,KeyError,TypeError) as error:
            self._error = str(error)
        self.changed.emit()

    def _source_arrived(self, revision, result):
        if revision != self._revision or not self._loaded or self._saving:
            return
        self._proxy_pending = False
        self._erase_editor.accept_source(*result)

    def _coverage_arrived(self,revision,result):
        if revision==self._revision and self._loaded and not self._saving:
            self._mask_editor.acceptCoverage(*result)

    @staticmethod
    def _work(action, path, original, draft, directory, sample=None):
        if bridge.sidecar_pcraft_path(path).exists():
            raise ValueError("This photo already has an editor project. Open it in the editor to edit it.")
        state, recipe, reason = bridge.recipe_state(path, set_aside=action == "load")
        if state == bridge.RECIPE_INVALID:
            raise ValueError(reason)
        # Light Depth and Atmosphere live in their own settings: a mask an earlier version stored for Light Depth is not part of
        # the draft.
        settings, extras = host_effects.load_extras(bridge.recipe_path(path), dict(recipe["cameraRaw"]) if recipe else {})
        if action == "load":
            settings = {**settings, **extras}
            return settings, str(bridge.saved_render_path(path) or ""), *(sample.read(path, settings) if sample else (None, ""))
        placed = draft.get("lightDepth")
        placed = placed if light_depth.normalized(placed)["amount"] > 0 else None   # kept beside the recipe only while it does something
        placed_air = draft.get("atmosphere")
        placed_air = placed_air if atmosphere.normalized(placed_air)["amount"] > 0 else None
        placed_blur = draft.get("bokeh")
        placed_blur = placed_blur if bokeh.normalized(placed_blur)["amount"] > 0 else None
        original, light_draft, draft = _without_light_depth(original), draft, _without_light_depth(draft)
        if settings != original and not (action not in ("save", "picture") and _saved_by_us(path, settings)):
            raise ValueError("The saved recipe changed. Reopen Edit RAW before applying more edits.")
        # What the renderer gets: the draft plus the masks Light Depth's settings make. They are built here, for every
        # preview and save, and are never stored (the saved recipe and the Mask tab only hold the person's own masks).
        full = _recipe({} if action == "whiteBalance" else light_draft, path)
        folder = str(Path(path).parent)
        # "picture": render and publish only the picture of the recipe on disk, never writing the recipe (editor_host renders a photo
        # the hosted editor saved this way).
        saving = action in ("save", "picture")
        write_root = str(edit_storage.ensure_edit_root(folder)) if saving else directory
        proc = bridge.launch_photocraft(None, read_root=folder, write_root=write_root,
                                       extra_args=("--raw-workspace",) if saving else ())
        try:
            job = getattr(_native_context, "job", None)
            if job:
                job.proc = proc
                if job.stopped.is_set():
                    job.cancel()
                    raise RuntimeError("RAW request cancelled")
            from .. import ai_denoise

            # Detail > Denoise: the denoised raw is developed in the photo's place once it has been made.
            source = proc.path_within_root(ai_denoise.render_source(path, draft))
            if not source:
                raise ValueError("The photo is outside the editor's read folder.")
            bridge.require_masks_support({"recipe": full}, getattr(proc.control, "camera_raw_masks_supported", False) is True)
            bridge.require_retouch_support({"recipe": full}, getattr(proc.control, "camera_raw_retouch_supported", False) is True)
            bridge.require_red_eye_support({"recipe": full}, getattr(proc.control, "camera_raw_red_eye_supported", False) is True)
            if not proc.control.full_render_supported:
                raise ValueError("This editor build cannot render RAW recipes.")
            if not saving:
                # Keep non-interactive host features in the recipe for save, outside this preview job.
                from ..full_render import _without_host_settings

                out = uuid4().hex
                reply = proc.control.call("app.render", {"source": source, "recipe": _without_host_settings(full), "out": out})
                name = reply.get("out")
                if name not in (out + ".png", out + ".tif", out + ".tiff"):
                    raise ValueError("The editor returned an unexpected preview path.")
                preview = Path(directory) / name
                if action == "whiteBalance":
                    try:
                        return _white_balance_sample(preview, *sample)
                    finally:
                        preview.unlink(missing_ok=True)
                try:
                    return _display_preview(preview)
                finally:
                    preview.unlink(missing_ok=True)
            # Render every draft before publishing it. Saving through the native dialog
            # commits the recipe first; a slow/failed render then makes the draft stale.
            from ..full_render import _without_host_settings
            from ..raw_crop import publish

            bridge.require_super_resolution_support(
                {"recipe": full}, getattr(proc.control, "camera_raw_super_resolution_supported", False),
            )
            signature = bridge.file_signature(path)
            if not signature:
                raise ValueError("The original photo could not be read")
            out = uuid4().hex
            reply = proc.control.call("app.render", {"source": source, "recipe": _without_host_settings(full), "out": out})
            name = reply.get("out")
            if name not in (out + ".png", out + ".tif", out + ".tiff"):
                raise ValueError("The editor returned an unexpected RAW render path")
            rendered = Path(write_root) / name
            try:
                state, current, reason = bridge.recipe_state(path)
                if state == bridge.RECIPE_INVALID:
                    raise ValueError(reason)
                if light_depth.strip_legacy((current or {}).get("cameraRaw", {})) != original:
                    raise ValueError("The saved recipe changed while the RAW was rendering")
                laid, scratch = host_effects.effects_file(rendered, path, {"atmosphere": placed_air, "bokeh": placed_blur})
                try:
                    published = publish(path, laid, draft, signature, recipe_on_disk=action == "picture")
                finally:
                    for stale in scratch:
                        stale.unlink(missing_ok=True)
                if action == "picture":
                    return published
                try:
                    light_depth.write_sidecar(bridge.recipe_path(path), placed)
                    atmosphere.write_sidecar(bridge.recipe_path(path), placed_air)
                    bokeh.write_sidecar(bridge.recipe_path(path), placed_blur)
                except OSError:
                    pass  # the saved mask still renders; only the Light Depth sliders start from their defaults next time
                return published
            finally:
                rendered.unlink(missing_ok=True)
        finally:
            proc.shutdown()

    def _arrived(self, revision, action, result, error):
        self._running = False
        self._native_action = ""
        self._native_job = None
        if action == "save":
            self._saving = False                      # whatever else happened meanwhile, the save is over
        if revision == self._revision:
            if action != "detail" or self._detail_mode:
                self._error = error
            if not error and action == "load":
                notice = bridge.take_quarantine_notice(self._path)
                if notice is not None:
                    self.recipeSetAside.emit(self._path, notice.message)
                self._original, self._preview, recovered, self._notice = (*result, None, "")[:4]
                self._baseline = self._preview
                self._draft = dict(self._original)
                self._loaded = True
                saving = self._saves.latest(self._path) if self._saves is not None else None
                if recovered is not None and saving is not None and light_depth.strip_legacy(recovered) == saving:
                    # its background save has not landed yet: that edit is the photo's own, not something to recover
                    self._original, self._draft = deepcopy(saving), deepcopy(saving)
                elif recovered is not None:
                    # One undo step back to the saved edit, so bringing a draft back is never a trap.
                    self._undo.append(("Recovered unsaved edits", dict(self._original)))
                    self._draft = light_depth.strip_legacy(recovered)   # a stored draft may still hold an earlier Light Depth mask
                    self._notice = "Unsaved edits from an earlier session were restored. Undo returns to the saved edit."
                # A saved display is already cropped; redevelop from the RAW before editing.
                self._preview = self._baseline = ""
                self._enqueue("preview")
            elif not error and action == "detail" and self._detail_mode:
                self._detail_size = _preview_dimensions(result)
                self._discard_preview(self._detail)
                self._detail = (raw_preview.put_frame(f"{self._owner}/{revision}/detail", result)
                                if not isinstance(result, str) else result)
            elif not error and action == "preview":
                self._discard_preview(self._preview)
                self._preview = result
                self._preview_size = _preview_dimensions(result)
                if min(self._full_size) <= 0:
                    self._full_size = list(self._preview_size)
            elif not error and action == "whiteBalance":
                self._set_values(result, "White balance sample")
            elif not error and action == "save":
                path = self._path
                self.cancel()
                self.saved.emit(path, result)
        if action == "detail" and isinstance(result, str) and result and result != self._detail:
            self._discard_preview(result)
        elif revision != self._revision and action == "preview" and result:
            self._discard_preview(result)
        if not self._path and not self._sessions:
            self._clear_files()
        if not self._timer.isActive():
            self._start_queued()
        self.changed.emit()

    def _discard_preview(self, path):
        if path and path.startswith("image://"):
            raw_preview.discard_frame(path)
            return
        if path and self._files and Path(path).parent == Path(self._files.name):
            Path(path).unlink(missing_ok=True)


def _saved_by_us(path, settings):
    """Whether the recipe on disk is one this app saved in the background (or is saving): the editor's own, not a change from
    elsewhere."""
    from . import edit_saves

    queue = edit_saves._instance
    if queue is None:
        return False
    if queue.pending(path):
        return True
    known = queue.saved(path)
    return known is not None and _without_light_depth(known) == settings


def _preview_dimensions(image):
    from PySide6.QtGui import QImageReader
    size = QImageReader(image).size() if isinstance(image, str) else image.size()
    return [size.width(), size.height()]


def _display_preview(path):
    """Keep native-resolution pixels in a display copy Qt can decode safely.

    PhotoCraft renders 16-bit RGB PNGs. Qt expands those to eight bytes per
    pixel, exceeding its default allocation limit on 45 MP cameras. Pillow
    decodes them into display RGB without changing the saved RAW recipe.
    """
    from PIL import Image, ImageOps
    from PySide6.QtGui import QImageReader

    target = Path(path).with_name(Path(path).stem + ".display.png")
    try:
        with Image.open(path) as source:
            profile = source.info.get("icc_profile")
            image = ImageOps.exif_transpose(source).convert("RGB")
            if profile:
                from io import BytesIO
                from PIL import ImageCms
                display_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(BytesIO(profile)),
                    display_profile, outputMode="RGB", renderingIntent=1, flags=0x2000)
                profile = display_profile.tobytes()
            # Keep full size whenever it fits, including the 8256 x 5504 Z7.
            # Larger sensors still need a bounded display fallback.
            limit = QImageReader.allocationLimit() * 1024 * 1024
            if limit > 0 and image.width * image.height * 4 > limit * .9:
                scale = math.sqrt(limit * .9 / (image.width * image.height * 4))
                image = image.resize((max(1, int(image.width * scale)),
                                      max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
            image.save(target, "PNG", icc_profile=profile, compress_level=1)
        return str(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _white_balance_sample(path, x, y):
    """Use PhotoCraft's relative-gain solver on a neutral sample from an unadjusted develop."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColorSpace, QImage, QImageReader

    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isEmpty():
        raise ValueError("The white balance sample could not be read.")
    px = min(size.width() - 1, int(x * size.width()))
    py = min(size.height() - 1, int(y * size.height()))
    reader.setClipRect(QRect(px - 2, py - 2, 5, 5).intersected(QRect(0, 0, size.width(), size.height())))
    image = reader.read()
    if image.isNull():
        raise ValueError("The white balance sample could not be read.")
    return _white_balance_from_image(image)


def _white_balance_from_image(image):
    from PySide6.QtGui import QColorSpace, QImage
    image = image.convertToFormat(QImage.Format.Format_RGBA64)
    if image.colorSpace().isValid():
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgbLinear))
    else:
        image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgbLinear))
    samples = []
    for row in range(image.height()):
        for column in range(image.width()):
            color = image.pixelColor(column, row)
            if color.alphaF() > 0:
                samples.append((color.redF(), color.greenF(), color.blueF()))
    if not samples:
        raise ValueError("Choose an opaque, neutral area for white balance.")
    r, g, b = (sum(sample[channel] for sample in samples) / len(samples) for channel in range(3))
    if min(r, g, b) <= 1e-6 or max(r, g, b) >= 0.99:
        raise ValueError("Choose a neutral area away from clipped highlights and deep shadows.")
    temperature = 100 * (b - r) / (0.35 * (r + b))
    tint = 400 * (1 - (2 * r * b / (r + b)) / g)
    return {"temperature": max(-100, min(100, temperature)), "tint": max(-100, min(100, tint))}
