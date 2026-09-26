# Manual smoke test

Run this by hand before and after any risky work item (Phase 3 onward, and any release). It covers what the automated suite cannot: a real display, a NAS, and feel. Use a **copy** of a folder of about 30 photos (include at least one RAW+JPEG pair and one folder on the NAS). Note the result of each step.

Baseline numbers (headless, synthetic, from `tests/perf_baselines.py`) are stored outside the repo in `C:\Users\tylle\ImageTriage-baselines\`. The interactive timings below have no headless equivalent; write yours down the first time so later runs have something to compare with.

| # | Step | Expected | Result / time |
|---|---|---|---|
| 1 | Start the app from a cold launch. | Window appears, no error dialog, last folder/position restored if the setting is on. Note seconds to usable. | |
| 2 | Open the local test folder (Open Folder). | Thumbnails fill in; column count matches the program-wide setting. Note seconds to first thumbnails. | |
| 3 | Open the NAS folder. | Loads without freezing the window; no "Not Responding". Note seconds. | |
| 4 | Mark a photo **Winner** (W), then again to un-mark. | Badge appears and clears. In Copy mode a copy appears in `_winners` and is removed on un-mark; the original is untouched. | |
| 5 | Mark a photo **Reject** (X). | Reject badge; it is no longer a winner. | |
| 6 | **Undo** the last three actions (Ctrl+Z x3). | Each state reverses in order; files in `_winners` match. | |
| 7 | **Move To** a new folder with 5 photos selected. | Progress dialog with speed and item count; files land in the destination; new folder appears in the tree without a restart. | |
| 8 | Undo the move. | Files return; the folder view refreshes. | |
| 9 | Delete a photo (safe trash), then undo. | Photo disappears then returns intact. | |
| 10 | Open a photo in the **popout**. Note seconds until the first slider responds. | No 10-15 s freeze on first use; the view does not flash at default zoom after an edit while zoomed. | |
| 11 | Drag an **Exposure** slider continuously, then a mask slider. | The image updates live while dragging (not only on release). Note whether it feels smooth. | |
| 12 | Crop: drag the crop box to a corner, click outside the box, drag the image. | No jump to the middle; clicking outside does not rotate; the image can be dragged inside the crop. | |
| 13 | Create a click-select mask on a cropped photo. | The hover preview and the committed mask both sit on the same subject. | |
| 14 | Save the edit, close the popout, reopen it. | The edit is still applied; the histogram shows only in Adjust and mask adjustments. | |
| 15 | Search for a folder path in the search bar; then open **Settings**, change one value, save, and reopen. | The folder opens; the value persisted; the AI setup check reports its current state without errors. | |

## After the run
- Any step that fails or feels slower than the recorded baseline is a regression: stop and report it against the work item just landed.
- Re-export the registry keys and re-run the baselines if the item touched settings or persistence:

```
reg export "HKCU\Software\Codex\Image Triage" registry_Codex_Image_Triage_<date>.reg /y
reg export "HKCU\Software\ImageTriage" registry_ImageTriage_<date>.reg /y
py -3.13 -m pytest tests/perf_baselines.py -s -p no:cacheprovider
```
