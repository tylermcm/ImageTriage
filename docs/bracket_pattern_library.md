# Bracket pattern library

`image_triage/bracket_patterns.py` lists how cameras bracket, keyed by behaviour, not by camera model. It is data only: nothing
in the app uses it yet. Phone computational capture (Apple, Google) is excluded on purpose.

Built from an outsourced research report that was then cross-checked against the cited pages on 1 November 2026. Each entry carries
a `status` (`confirmed`, `partial`, `inferred`) and its source URLs.

## Entries

Real exposure ladders (the EV maths can find them): `ae_mode_mapped` (Nikon, Olympus/OM, Leica), `ae_canon`, `ae_sony`,
`ae_configurable` (Fujifilm, Panasonic, Pentax), `in_camera_hdr`.

Never to be mistaken for an exposure ladder: `flash_bracket`, `iso_derived_copies`, `iso_captured_frames`, `wb_variants`,
`tone_style_variants`, `focus_bracket`, `aperture_dof_bracket`, `motion_shutter_bracket`, `pixel_shift`, `multi_exposure`,
`interval_sequence`.

## What the cross-check found

Corrections to the report, already reflected in the entries:

- OM ISO bracketing is not one behaviour. The OM-1 manual says one capture is processed into three copies. The OM-5 and E-M5 III
  manuals say the camera shoots three frames with shutter and aperture fixed. Two entries.
- Canon `BracketMode` is at FileInfo index 2 (the report said 3); `BracketValue` is 4 and `BracketShotNumber` is 5.
- The Sony two-frame positive/negative bracket (firmware 4.00) is on the A7 IV bracket page, not the page the report cited.
- The 5D II manual says shutter or aperture, +/-2 stops, three shots. It does not mention ISO.

Not confirmed, left marked as such:

- Leica SL3 manual: the download was blocked, so the SL3 mapping and order are unverified. Q3 spec (3 or 5 frames, 3 EV) is confirmed.
- Nikon's default shooting order is 0,-,+ per the owner (Z7 II). The Nikon pages read do not state it; it is a custom setting (e7).
- Canon `AutoExposureBracketing` value list (the tag exists; values were not read), and which Canon bodies the 2/3/5/7 and +/-3 pages describe.
- Fujifilm X-T20 step range.
- Ricoh gives multi-exposure counts as 2-2000 in one place and 2-200 in another.
- Apple ProRAW fusion and the iPhone HDR page, Google HDR+ frame counts: phones are out of scope here.

## Next

The pattern detector (EV ladder plus tag evidence) and the scene confirmation are not written. The library is the vocabulary for them.
