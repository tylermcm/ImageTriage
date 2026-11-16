# Auto tone and white balance

Two switches in the Adjust tab: **Tone** (in the Light section) and **Auto** beside the white-balance dropper (Color section).
Press once to apply, press again to take it off (back to the values from before, one undo step each way). While one is on, a
**Strength** slider appears under it and changes what is applied at once (0 = nothing, 100 = everything Auto asked for).

`image_triage/auto_tone.py` works from the editor's own preview (display pixels with the edits so far), so every result is a
*change* to the current slider values; asking again after an edit asks for less. Rules, not a model:

- **Exposure:** median to mid-grey (18% linear), damped (x0.75), at most 2 stops, and never past the point where the brightest
  0.5% clips.
- **Highlights / shadows:** from the 98th / 10th percentile after the exposure change, plus the share of clipped pixels.
- **Whites / blacks:** end points so about 0.2% clips at each end.
- **Contrast:** from the spread between the 5th and 95th percentile. **Vibrance:** a lift for flat colour pictures only.
- **White balance:** shades-of-grey (norm 6) over near-neutral, unclipped pixels only; skipped when fewer than 3% qualify (a
  lake or a forest is not a cast) or the cast is under 6 points; only part of a cast is taken (60%, at most 30 points a click).
  Uses the same temperature/tint arithmetic as the white-balance dropper.

Checked on the lake NEF: sky highlights recovered (2.8% clipped -> 0), shadows lifted, the strong blue mood kept (temperature +30).

## Not done yet
- Sync: applying Auto to a batch of frames (the editor model has `auto()`; the sync panel does not call it yet).
- Rules were tuned on a handful of photos; a learned style profile is the later step (see `creative_tools_plan.md`).
