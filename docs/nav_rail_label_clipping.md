# Left rail: "Collections" and "PocketDrop" clip

Written for Codex, from a design discussion with Tyler.

**Decided and built: option 4, icon-only.** Tyler chose it because it matches the pinned tool buttons below the destinations, which have no text either. See `NavRail.SHOW_LABELS` and `_NavButton.set_label_visible` in `image_triage/ui/nav_rail.py`: the labels are no longer painted, each tooltip now leads with the name ("Collections: Collections" is avoided; "PocketDrop: Send files to and from your phone"), the accessible name is unchanged, and the pill is shorter with a slightly larger icon (`BUTTON_H_RATIO` 40, `ICON_RATIO` 22, floors 38 px and 18 px). Setting `SHOW_LABELS = True` restores the old look. The rest of this note is the original comparison.
Tyler's constraint was to fix the clipping **without making the label text smaller or harder to read**.

Visual comparison (the current rail plus all four options at the same scale):
<https://claude.ai/artifact/G5LLLDC2PV2LzGsbiA7QDP>. The source is `docs/mockups/nav_rail_labels.html` in the
`collections` worktree (not committed).

## The problem

The left icon rail is about 64 px wide at a typical window size. Its text labels are about 12 px.
"Library" and "Faces" fit. "Collections" (11 letters) and "PocketDrop" (10 letters) lose their first and last
letters, so the selected tab reads "Collecti..." on screen.

## Where it lives

| What | Where |
| --- | --- |
| Tab keys, labels, tooltips | `LEFT_NAV_DESTINATIONS` in `image_triage/window.py` (around line 476) |
| Rail width follows the window | `layout_ratios.RAIL_W = 0.025` in `image_triage/ui/layout_ratios.py`; applied in `AppearanceController.apply_layout_ratios` via `left_nav_rail.apply_width(px(RAIL_W, width, minimum=56))` |
| Button, icon and label sizes | `metrics_for(width)` and `_style_button` in `image_triage/ui/nav_rail.py` (`label_px`, `button_width`, `MIN_LABEL_PX`) |
| Page titles that repeat the names | `window.py` (`"projects": "Collections"` and similar) and the docs pages |

The label size is set through each button's own stylesheet in `_style_button`, because a widget's stylesheet beats the application one.

## The options

1. **Shorter labels (recommended).** Rename the two long tabs, for example "Sets" (or "Albums") and "Drop" (or "Pocket").
   The tooltips and page titles keep the full names. Same rail width, same text size. Cost: a new word for existing users.
   Only the label strings in `LEFT_NAV_DESTINATIONS` change. The tooltip text already exists there.
2. **Wider rail.** About 88 px instead of 64 px. Keeps every real name. Cost: about 24 px less for the grid on every screen,
   and a longer label later would break it again. If chosen, do not hard-code a width. Size the rail from the measured width of
   the longest label (`QFontMetrics.horizontalAdvance` at `label_px`, plus side padding) so it follows font size and DPI.
3. **Two-line labels.** "Pocket / Drop" would fit. "Collections" is one long word and cannot wrap, so it still clips.
   Rows would also end up different heights. Not recommended.
4. **Icons only, name on hover or when selected.** A rail of about 48 px, with a flyout label on hover and keyboard focus.
   Smallest rail and room for more tabs, but the icons must be learned. Needs a tooltip or flyout that also appears on keyboard focus.

Elided text ("Collect...") was considered and rejected: it looks worse than clipping.

## If option 1 is chosen

- Change only the label field in `LEFT_NAV_DESTINATIONS`. Keep the key (`"collections"`, `"pocketdrop"`) as is, because it is used for navigation and saved state.
- Check that the new labels fit at the smallest rail width (`MIN_RAIL_PX`) and at the largest window sizes.
- Search for places that show the label text elsewhere: the command palette ("Open Collections"), help pages in `image_triage/ui/docs/content/`
  (the export page says "Open it from the **PocketDrop** button on the left rail"), and any test that reads the button text.
- Run the rail and layout tests, and the stylesheet golden test if any stylesheet text changes.

## If option 2 is chosen

- Add a helper that returns the rail width needed for the longest label, and take the larger of that and the ratio width.
- Keep the 56 px floor logic, and make sure the sidebar pane and grid splitter still get their ratios (`LIBRARY_PANEL_W = RAIL_W + LIBRARY_PANE_W` in `layout_ratios.py` assumes the rail share).
- Check small windows, where a wider rail costs the most.

## Not decided

Which option to use, and the exact words for option 1. Ask Tyler before changing labels, since the names also appear in the help pages and the command palette.
