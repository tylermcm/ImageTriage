"""Library of bracketing behaviours (what cameras do), keyed by behaviour, not by camera model.

Qt-free data only. Nothing here detects anything yet: the detector will match a run of
photos against these entries and then confirm the scene separately. Phone computational
capture (Apple, Google) is deliberately excluded.

`status` records how well the entry is backed:
  confirmed  - checked against a manufacturer manual or the ExifTool tag docs
  partial    - some parts confirmed, the rest inferred or only seen on some models
  inferred   - reasoning from the other entries, not from a source
Details of what was and was not verified are in docs/bracket_pattern_library.md.
"""

from __future__ import annotations

from dataclasses import dataclass

CONFIRMED = "confirmed"
PARTIAL = "partial"
INFERRED = "inferred"
STATUSES = (CONFIRMED, PARTIAL, INFERRED)

# What can change between frames of a sequence.
SHUTTER = "shutter"
APERTURE = "aperture"
ISO = "iso"
FLASH = "flash_power"
WHITE_BALANCE = "white_balance"
TONE_STYLE = "tone_or_style"
FOCUS = "focus"
SENSOR_POSITION = "sensor_position"
NOTHING = "nothing_intended"

ORDER_ZERO_MINUS_PLUS = "0,-,+"
ORDER_MINUS_ZERO_PLUS = "-,0,+"
ORDER_PLUS_ZERO_MINUS = "+,0,-"


@dataclass(frozen=True, slots=True)
class TagRef:
    """A maker-note or EXIF field that can identify the pattern."""

    maker: str
    name: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BracketPattern:
    id: str
    label: str
    # True when the physical exposure (shutter, aperture, ISO together) follows a ladder,
    # so the EV maths can find it. False means EV maths alone must never call it a bracket.
    ev_ladder: bool
    varies: tuple[str, ...]
    makers: tuple[str, ...]
    status: str
    summary: str
    modes: str = ""
    frame_counts: tuple[int, ...] = ()
    step_ev: tuple[float, ...] = ()
    orders: tuple[str, ...] = ()
    one_press_many_files: bool = False
    tags: tuple[TagRef, ...] = ()
    caveats: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"{self.id}: unknown status {self.status!r}")


_THIRD_STEPS = (0.3, 0.7, 1.0, 1.3, 1.7, 2.0, 2.3, 2.7, 3.0)

PATTERNS: tuple[BracketPattern, ...] = (
    BracketPattern(
        id="ae_mode_mapped",
        label="Exposure bracket, variable set by exposure mode",
        ev_ladder=True,
        varies=(SHUTTER, APERTURE),
        makers=("Nikon", "Olympus/OM System", "Leica"),
        status=CONFIRMED,
        summary=(
            "P changes shutter and aperture, A and M change shutter, S changes aperture. "
            "Auto ISO can move ISO: Nikon varies it when exposure limits are reached in P/S/A; "
            "Olympus varies ISO in M when ISO and ISO-Auto are both automatic."
        ),
        modes="P shutter+aperture; A shutter; S aperture; M shutter (ISO if Auto ISO, Olympus)",
        frame_counts=(2, 3, 5, 7, 9),
        step_ev=_THIRD_STEPS,
        orders=(ORDER_ZERO_MINUS_PLUS, ORDER_MINUS_ZERO_PLUS),
        tags=(
            TagRef("Nikon", "BracketSet", "0x0109: 1 AE/Flash, 2 AE, 3 Flash, 4 WB, 5 Active-D Lighting"),
            TagRef("Nikon", "BracketProgram", "0x010a: 3F/5F/7F/9F and directional two-frame values"),
            TagRef("Nikon", "BracketIncrement", "0x010b: 0.3, 0.5, 1, 2, 3 EV"),
            TagRef("Nikon", "AutoBracketOrder", "older bodies: 0 = 0,-,+  1 = -,0,+"),
            TagRef("Nikon", "AutoBracketModeM", "older bodies: manual-mode variable"),
        ),
        caveats=(
            "Nikon: increments of 2 EV or more cap the sequence at 5 frames.",
            "Nikon custom setting e6 changes the manual-mode variable (shutter, shutter+aperture, aperture, flash only).",
            "Frame counts: Nikon 3-9 (Z8 manual), Olympus 2/3/5/7 (E-M5 III) or 3 only (E-PL1). Leica SL3 values unverified (manual download blocked).",
            "Nikon default shooting order not confirmed from the pages read; the order is a custom setting.",
        ),
        sources=(
            "https://onlinemanual.nikonimglib.com/d850/en/11_exposure_07.html",
            "https://onlinemanual.nikonimglib.com/z8/en/bracketing_36.html",
            "https://exiftool.org/TagNames/NikonSettings.html",
            "https://exiftool.org/TagNames/NikonCustom.html",
            "https://download.omsystem.com/pages/inst/em5m3/manual_em5m3_ENU.pdf",
            "https://download.omsystem.com/pages/inst/epl1/manual_epl1_ENU.pdf",
        ),
    ),
    BracketPattern(
        id="ae_canon",
        label="Canon exposure bracket (AEB)",
        ev_ladder=True,
        varies=(SHUTTER, APERTURE, ISO),
        makers=("Canon",),
        status=PARTIAL,
        summary=(
            "Classic bodies: three shots, shutter or aperture, up to +/-2 stops in 1/3 steps, "
            "default order 0,-,+ or -,0,+ via a custom function. Newer bodies: 2/3/5/7 shots, "
            "+/-3 stops, sequences 0,-,+ / -,0,+ / +,0,-, one press per frame or one burst."
        ),
        modes="creative modes; flash and bulb excluded on the 5D II",
        frame_counts=(2, 3, 5, 7),
        step_ev=(0.33, 0.5, 1.0, 2.0, 3.0),
        orders=(ORDER_ZERO_MINUS_PLUS, ORDER_MINUS_ZERO_PLUS, ORDER_PLUS_ZERO_MINUS),
        tags=(
            TagRef("Canon", "AutoExposureBracketing", "ShotInfo index 16: on and which shot (values not read)"),
            TagRef("Canon", "AEBBracketValue", "ShotInfo index 17"),
            TagRef("Canon", "SequenceNumber", "ShotInfo index 9, some models only"),
            TagRef("Canon", "BracketMode", "FileInfo index 2: 0 Off, 1 AEB, 2 FEB, 3 ISO, 4 WB"),
            TagRef("Canon", "BracketValue", "FileInfo index 4"),
            TagRef("Canon", "BracketShotNumber", "FileInfo index 5"),
        ),
        caveats=(
            "The 5D II manual (shutter or aperture, +/-2) is confirmed. Which Canon bodies the 2/3/5/7 and +/-3 pages describe could not be established.",
            "Which of the two tag families a file carries depends on the model and format.",
            "Exports often strip Canon maker notes.",
        ),
        sources=(
            "https://gdlp01.c-wss.com/gds/0/0300004270/02/eos5dmkii-im5-c-en.pdf",
            "https://cam.start.canon/en/C017/manual/html/UG-09_Custom_0030.html",
            "https://exiftool.org/TagNames/Canon.html",
        ),
    ),
    BracketPattern(
        id="ae_sony",
        label="Sony exposure bracket",
        ev_ladder=True,
        varies=(SHUTTER, ISO),
        makers=("Sony",),
        status=CONFIRMED,
        summary=(
            "In M mode with ISO AUTO the bracket changes ISO; with fixed ISO it changes shutter. "
            "Continuous bracket runs on one hold; single bracket needs one press per frame. "
            "With flash the camera brackets flash output instead."
        ),
        modes="P/A/S/M; unavailable in Intelligent Auto, Superior Auto, Scene Selection, Sweep Panorama",
        frame_counts=(2, 3, 5, 9),
        step_ev=(0.3, 0.5, 0.7, 1.0, 2.0, 3.0),
        orders=(ORDER_ZERO_MINUS_PLUS,),
        tags=(
            TagRef("Sony", "ReleaseMode", "0xb049: 5 Exposure Bracketing, 6 WB Bracketing, 8 DRO Bracketing"),
            TagRef("Sony", "SequenceNumber", "0xb04a: shot number in a continuous burst"),
            TagRef("Sony", "ExposureBracketShotNumber", "FocusInfo index 47, A200-A900 era"),
        ),
        caveats=(
            "Two-frame positive-only or negative-only bracketing needs firmware 4.00 on the A7 IV.",
            "2 and 3 EV steps are listed only with 3 or 5 images; nine frames are for smaller steps (one older generation page).",
            "Shot-number tags in FocusInfo are for old bodies; newer bodies rely on ReleaseMode.",
        ),
        sources=(
            "https://helpguide.sony.net/ilc/2110/v1/en/contents/TP1000640163.html",
            "https://helpguide.sony.net/ilc/1520/v1/en/contents/TP0001077125.html",
            "https://exiftool.org/TagNames/Sony.html",
        ),
    ),
    BracketPattern(
        id="ae_configurable",
        label="Configurable exposure bracket (frames, step, sequence)",
        ev_ladder=True,
        varies=(SHUTTER, APERTURE, ISO),
        makers=("Fujifilm", "Panasonic", "Pentax"),
        status=CONFIRMED,
        summary=(
            "Fujifilm AE BKT (2/3/5/7/9 frames, 1/3 to 3 EV on current bodies, single or continuous), "
            "Panasonic exposure bracket (3/5/7 frames, 1/3, 2/3, 1 EV, +/-3 max, sequence setting), "
            "Pentax (2/3/5 frames)."
        ),
        modes="normal exposure modes",
        frame_counts=(2, 3, 5, 7, 9),
        step_ev=(0.3, 0.5, 0.7, 1.0, 2.0, 3.0),
        orders=(ORDER_ZERO_MINUS_PLUS, ORDER_MINUS_ZERO_PLUS),
        tags=(
            TagRef("Panasonic", "BurstMode", "0x002a: 2 AEB, 3 Focus Bracketing, 8 WB, 18 Aperture Bracketing"),
            TagRef("Panasonic", "BracketSettings", "0x0045: 3/5/7 images, 0/-/+ or -/0/+"),
            TagRef("Panasonic", "SequenceNumber", "0x002b"),
            TagRef("Fujifilm", "AutoBracketing", "0x1100: 1 On (X-T3 2 = Pre-shot; other models 2 = No flash & flash)"),
            TagRef("Fujifilm", "SequenceNumber", "0x1101"),
            TagRef("Pentax", "DriveMode2", "bit 6 = Exposure Bracket"),
            TagRef("Pentax", "ExposureBracketStepSize", "index 8: 0.3 to 2 EV"),
            TagRef("Pentax", "BracketShotNumber", "index 9: e.g. 2 of 3, 3 of 5"),
            TagRef("Pentax", "AutoBracketing", "0x0018: first value is step in EV"),
        ),
        caveats=(
            "Fujifilm older bodies (X-T20) offer fewer steps; AutoBracketing value meanings change by model.",
            "Pentax has the clearest per-frame tags (shot x of y); keep them as evidence when present.",
        ),
        sources=(
            "https://fujifilm-dsc.com/en/manual/x-e3/technical_notes/spec/index.html",
            "https://fujifilm-dsc.com/en/manual/x-t3/menu_shooting/shooting_setting/index.html",
            "https://eww.pavc.panasonic.co.jp/dscoi/DC-G9M2/html/DC-G9M2_DVQP3010_eng/0053.html",
            "https://help.na.panasonic.com/answers/features-and-specifications-lumix-g-series-dc-gh5/",
            "https://www.ricoh-imaging.co.jp/english/support/qa/k-3-3/",
            "https://exiftool.org/TagNames/Pentax.html",
            "https://exiftool.org/TagNames/FujiFilm.html",
            "https://exiftool.org/TagNames/Panasonic.html",
        ),
    ),
    BracketPattern(
        id="flash_bracket",
        label="Flash power bracket",
        ev_ladder=False,
        varies=(FLASH,),
        makers=("Nikon", "Sony", "Canon", "Olympus/OM System"),
        status=PARTIAL,
        summary=(
            "Flash output steps while ambient shutter, aperture and ISO may be identical. "
            "Nikon can bracket flash alone or with AE; Sony switches an exposure bracket to flash power when flash is used."
        ),
        frame_counts=(3,),
        orders=(ORDER_ZERO_MINUS_PLUS,),
        tags=(
            TagRef("Nikon", "BracketSet", "3 = Flash, 1 = AE/Flash"),
            TagRef("Canon", "BracketMode", "2 = FEB"),
        ),
        caveats=(
            "An EV-only detector sees identical exposures; never reject it as 'not a bracket'.",
            "Flash recharge dominates spacing, so timing is slower.",
        ),
        sources=(
            "https://onlinemanual.nikonimglib.com/d850/en/18_menu_guide_05_e06.html",
            "https://helpguide.sony.net/ilc/2040/v1/en/contents/TP1000384685.html",
            "https://exiftool.org/TagNames/Canon.html",
        ),
    ),
    BracketPattern(
        id="iso_derived_copies",
        label="ISO bracket recorded as processed copies",
        ev_ladder=False,
        varies=(ISO,),
        makers=("Fujifilm", "Olympus/OM System (OM-1)"),
        status=CONFIRMED,
        summary=(
            "One press records one capture; the camera makes two more copies by shifting sensitivity "
            "by +/-1/3, 2/3 or 1 EV (Fujifilm) or 0.3, 0.7, 1 EV (OM-1). Shutter and aperture are identical, "
            "timestamps are nearly identical."
        ),
        frame_counts=(3,),
        step_ev=(0.3, 0.7, 1.0),
        orders=(ORDER_ZERO_MINUS_PLUS,),
        one_press_many_files=True,
        caveats=(
            "Not real exposures, so EV maths computed from shutter and aperture shows no ladder.",
            "Only the OM-1 manual states the one-capture behaviour; see iso_captured_frames for OM-5 and E-M5 III.",
        ),
        sources=(
            "https://fujifilm-dsc.com/en/manual/x-t5/taking_photo/bracketing/",
            "https://download.omsystem.com/pages/inst/om1/manual_om1_v1.7_ENU.pdf",
        ),
    ),
    BracketPattern(
        id="iso_captured_frames",
        label="ISO bracket recorded as separate captures",
        ev_ladder=False,
        varies=(ISO,),
        makers=("Olympus/OM System (OM-5, E-M5 III)",),
        status=CONFIRMED,
        summary=(
            "Three frames per press with shutter and aperture fixed; sensitivity is current, negative, positive "
            "(0.3, 0.7 or 1 EV). Computed EV from ISO does step, unlike the derived-copy version."
        ),
        frame_counts=(3,),
        step_ev=(0.3, 0.7, 1.0),
        orders=(ORDER_ZERO_MINUS_PLUS,),
        caveats=(
            "The same menu item behaves differently across OM models; do not assume derived copies.",
        ),
        sources=(
            "https://download.omsystem.com/pages/inst/em5m3/manual_em5m3_ENU.pdf",
            "https://download.omsystem.com/pages/inst/om5/manual_om5_ENU.pdf",
        ),
    ),
    BracketPattern(
        id="wb_variants",
        label="White balance bracket",
        ev_ladder=False,
        varies=(WHITE_BALANCE,),
        makers=("Nikon", "Canon", "Sony", "Fujifilm", "Panasonic", "Olympus/OM System"),
        status=CONFIRMED,
        summary=(
            "Colour differs, brightness does not. Nikon, Fujifilm and some others make multiple copies from one "
            "capture; Panasonic records three images. Often unavailable with RAW."
        ),
        frame_counts=(3, 5, 7, 9),
        tags=(
            TagRef("Nikon", "BracketSet", "4 = White Balance"),
            TagRef("Canon", "BracketMode", "4 = WB"),
            TagRef("Canon", "WBBracketMode", "FileInfo index 9"),
            TagRef("Sony", "ReleaseMode", "6 = White Balance Bracketing"),
            TagRef("Fujifilm", "WhiteBalanceBracketing", "0x1102: +/-1, 2, 3"),
            TagRef("Panasonic", "BurstMode", "8 = White Balance Bracketing"),
        ),
        caveats=("File count can exceed the number of exposures. EV maths cannot see this.",),
        sources=(
            "https://onlinemanual.nikonimglib.com/d850/en/11_exposure_07.html",
            "https://exiftool.org/TagNames/Sony.html",
            "https://exiftool.org/TagNames/FujiFilm.html",
        ),
    ),
    BracketPattern(
        id="tone_style_variants",
        label="Tone or style bracket (Active D-Lighting, DRO, film simulation, art filter)",
        ev_ladder=False,
        varies=(TONE_STYLE,),
        makers=("Nikon", "Sony", "Fujifilm", "Olympus/OM System"),
        status=CONFIRMED,
        summary=(
            "Same exposure, different processing. Nikon ADL 2-5 shots, Sony DRO Lo (1/2/3) or Hi (1/3/5), "
            "Fujifilm film simulation copies from one shot, Olympus art filter copies."
        ),
        one_press_many_files=True,
        tags=(
            TagRef("Nikon", "BracketSet", "5 = Active-D Lighting"),
            TagRef("Sony", "ReleaseMode", "8 = DRO Bracketing"),
            TagRef("Sony", "DynamicRangeOptimizerBracket", "FocusInfo index 46: 1 Low, 2 High"),
        ),
        caveats=("EV maths cannot recognise these.",),
        sources=(
            "https://helpguide.sony.net/ilc/2420/v1/en/contents/0407_drivemode_dro_bracket.html",
            "https://onlinemanual.nikonimglib.com/d850/en/11_exposure_07.html",
        ),
    ),
    BracketPattern(
        id="focus_bracket",
        label="Focus bracket / focus shift",
        ev_ladder=False,
        varies=(FOCUS,),
        makers=("Canon", "Nikon", "Sony", "Fujifilm", "Panasonic", "Olympus/OM System"),
        status=CONFIRMED,
        summary=(
            "Focus distance advances (usually near to far); exposure intended constant. Counts: Canon 2-999, "
            "Panasonic up to 999, Nikon up to 300, Olympus configurable. Exposure smoothing or a first-frame "
            "exposure lock may be offered."
        ),
        frame_counts=(),
        tags=(
            TagRef("Canon", "FocusBracketing", "CameraSettings index 50; FocusBracketingInfo 0x4053 holds ImageCount, FocusIncrement, ExposureSmoothing"),
            TagRef("Nikon", "FocusShiftShooting", "SeqInfo tables"),
            TagRef("Nikon", "FocusShiftNumberShots", "menu settings"),
            TagRef("Olympus", "FocusBracketStepSize", "0x0308"),
            TagRef("Panasonic", "BurstMode", "3 = Focus Bracketing"),
            TagRef("Panasonic", "FocusBracket", "0x00bd: signed focus offset"),
        ),
        caveats=(
            "Exposure smoothing can make brightness drift slightly; do not require exactly constant exposure.",
            "Long sequences (tens to hundreds of frames), cadence from seconds to a fraction of a second.",
        ),
        sources=(
            "https://cam.start.canon/ky/C017/manual/html/UG-03_Shooting-1_0270.html",
            "https://onlinemanual.nikonimglib.com/z8/en/psm_focus_shift_shooting_155.html",
            "https://exiftool.org/TagNames/Canon.html",
            "https://exiftool.org/TagNames/Panasonic.html",
        ),
    ),
    BracketPattern(
        id="aperture_dof_bracket",
        label="Aperture / depth-of-field bracket",
        ev_ladder=False,
        varies=(APERTURE,),
        makers=("Panasonic", "Pentax"),
        status=CONFIRMED,
        summary=(
            "Panasonic aperture bracket (A mode, or M with ISO AUTO; 3, 5 or all apertures) and Pentax K-3 III "
            "depth-of-field bracketing (3 shots, Av/TAv, exposure held constant)."
        ),
        frame_counts=(3, 5),
        tags=(TagRef("Panasonic", "BurstMode", "18 = Aperture Bracketing"),),
        caveats=("Computed total EV stays about constant even though aperture steps. Do not treat as an exposure ladder.",),
        sources=(
            "https://eww.pavc.panasonic.co.jp/dscoi/DC-G9M2/html/DC-G9M2_DVQP3010_eng/0053.html",
            "https://www.ricoh-imaging.co.jp/english/support/qa/k-3-3/",
        ),
    ),
    BracketPattern(
        id="motion_shutter_bracket",
        label="Motion (shutter) bracket with exposure held constant",
        ev_ladder=False,
        varies=(SHUTTER,),
        makers=("Pentax",),
        status=CONFIRMED,
        summary="Pentax K-3 III motion bracketing: three shots, shutter steps, exposure held constant, Av/TAv only.",
        frame_counts=(3,),
        step_ev=(0.33, 0.5),
        caveats=("Shutter changes but computed EV stays constant, so it can pass for an AE bracket when only shutter is checked.",),
        sources=("https://www.ricoh-imaging.co.jp/english/support/qa/k-3-3/",),
    ),
    BracketPattern(
        id="pixel_shift",
        label="Pixel shift",
        ev_ladder=False,
        varies=(SENSOR_POSITION,),
        makers=("Pentax", "Fujifilm", "Nikon"),
        status=CONFIRMED,
        summary=(
            "Sensor moves between frames, exposure intended identical. Pentax K-1 II records 4 images; "
            "Fujifilm X-T5 records 20 RAW files at half-pixel shifts."
        ),
        frame_counts=(4, 20),
        tags=(TagRef("Fujifilm", "AutoBracketing", "6 = Pixel Shift (not X-T3)"),),
        caveats=("Looks like a zero-EV bracket. Never classify as AEB.", "Nikon pixel shift exists (PixelShiftNumberShots tag); counts not checked."),
        sources=(
            "https://www.ricoh-imaging.co.jp/english/products/k-1-2/feature/02.html",
            "https://app.fujifilm-dsc.com/en/manual/x-t5/menu_shooting/shooting_setting/",
        ),
    ),
    BracketPattern(
        id="multi_exposure",
        label="Multiple exposure (composite)",
        ev_ladder=False,
        varies=(NOTHING,),
        makers=("Pentax", "Fujifilm", "Nikon"),
        status=CONFIRMED,
        summary=(
            "Deliberate composites. Pentax K-1 II lists 2-2000 images in one place and 2-200 in another."
        ),
        tags=(
            TagRef("Pentax", "DriveMode2", "bit 7 = Multiple Exposure"),
            TagRef("Nikon", "MultiExposureShots", "menu settings"),
        ),
        caveats=("Not a bracket; may be adjacent to real brackets in a folder.",),
        sources=(
            "https://www.ricoh-imaging.co.jp/english/support/qa/k-1-2/",
            "https://exiftool.org/TagNames/Pentax.html",
        ),
    ),
    BracketPattern(
        id="interval_sequence",
        label="Interval or time-lapse sequence",
        ev_ladder=False,
        varies=(NOTHING,),
        makers=("Nikon", "Fujifilm", "Pentax", "Olympus/OM System"),
        status=PARTIAL,
        summary=(
            "Regular cadence over minutes. Brightness changing with daylight can imitate a smooth EV ladder."
        ),
        tags=(
            TagRef("Nikon", "IntervalShooting", "SeqInfo tables"),
            TagRef("Nikon", "IntervalFrame", "SeqInfo tables"),
        ),
        caveats=("A regular multi-second cadence is evidence against ordinary handheld AEB.",),
        sources=("https://exiftool.org/TagNames/Nikon.html", "https://www.ricoh-imaging.co.jp/english/support/qa/k-1-2/"),
    ),
    BracketPattern(
        id="in_camera_hdr",
        label="In-camera HDR (source frames and merged output)",
        ev_ladder=True,
        varies=(SHUTTER, APERTURE),
        makers=("Olympus/OM System", "Pentax"),
        status=PARTIAL,
        summary=(
            "Olympus HDR button: 3, 5 or 7 frames 2 or 3 EV apart as source frames; HDR1/HDR2 take four "
            "exposures and merge in camera. Pentax HDR Capture: three exposures (standard, under, over) merged."
        ),
        frame_counts=(3, 4, 5, 7),
        step_ev=(1.0, 2.0, 3.0),
        caveats=(
            "Some modes keep the source frames, others only the merged file, so file adjacency alone is not enough.",
        ),
        sources=(
            "https://learnandsupport.getolympus.com/learn-center/photography-tips/landscapes-nature/exposure-bracketing-achieving-more-accurate-hdr",
            "https://www.ricoh-imaging.co.jp/english/support/qa/k-3-3/",
        ),
    ),
)

_BY_ID = {pattern.id: pattern for pattern in PATTERNS}


def get_pattern(pattern_id: str) -> BracketPattern:
    return _BY_ID[pattern_id]


def exposure_ladder_patterns() -> tuple[BracketPattern, ...]:
    """Patterns whose physical exposure follows a ladder that the EV maths can find."""
    return tuple(pattern for pattern in PATTERNS if pattern.ev_ladder)


def patterns_not_found_by_ev() -> tuple[BracketPattern, ...]:
    """Patterns a detector must recognise separately and must never mistake for an AE ladder."""
    return tuple(pattern for pattern in PATTERNS if not pattern.ev_ladder)


def tags_for_maker(maker: str) -> tuple[tuple[BracketPattern, TagRef], ...]:
    wanted = maker.casefold()
    return tuple(
        (pattern, tag)
        for pattern in PATTERNS
        for tag in pattern.tags
        if tag.maker.casefold() == wanted
    )
