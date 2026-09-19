# AI Review mode retirement — what it disconnected

September 2026. The app now always runs in manual review. AI Review as a separate *mode*
had already faded once the photo cards were redesigned, and the left sidebar rework
(labelled rail that swaps whole panes: Folders, Faces, Collections) removed the last
visible switch into it.

## How it was retired

- `MainWindow._set_ui_mode` always lands in manual, whatever it is asked for.
- `MainWindow._handle_mode_tab_changed` pulls any request for tab 1 back to 0. Every
  remaining route into AI mode (AI toolbar buttons, the toolbar editor's AI target,
  workflow presets) goes through this one guard.
- The `mode_tabs` bar ("Manual Review | AI Review") is hidden. It stays only as the
  state holder the rest of the window reads.
- Removed: View ▸ Mode menu, the `manual_mode` / `ai_mode` actions, the palette's
  "Switch To Manual/AI Review" commands, the left Browse / AI · Activity tabs, and
  the post-run jumps into AI Review (`_handle_ai_run_finished`, and quiet reloads
  with `switch_to_ai_tab`).

Everything below still exists in code but can no longer be reached, or no longer
behaves as it did. Nothing was rebuilt in manual mode; these are the items to decide on.

## Disconnected

1. **AI badges on cards.** `grid.set_show_ai_annotations(...)` is now always
   `False`, so AI Pick, confidence buckets, and the orange Disputed badge never
   paint. (Consistent with the card redesign, but the data is still computed.)
2. **AI results no longer load on their own.** `_restore_ai_results` skips unless
   `force=True` outside AI mode. The async hidden-results load that ran on entering
   AI Review (`_schedule_hidden_ai_results_load`) is never triggered. An AI run still
   sets `_ai_bundle` when it finishes, but nothing shows it.
3. **AI Output Tags filter panel** (Winner / Reject / Review / AI Miss swatches in
   the old AI · Activity tab) was deleted with the tabs:
   `_build_generated_ai_activity_panel`, `_toggle_ai_activity_tag_filter`,
   `_sync_left_ai_activity_filter_buttons`, `_ai_activity_tag_label`,
   `_set_ai_activity_swatch_style`, `AI_ACTIVITY_TAG_SPECS`. Recoverable from
   commit `76ad91b`. The underlying filter fields (`ai_cull_bucket`,
   `ai_workflow_tag`) still work through the filter dialog.
4. **"Dispute current AI result"** is enabled only when `_ui_mode == "ai"`, so it
   is permanently disabled.
5. **"Open AI Review"** (`_open_current_ai_review`) now just loads results and
   stays in manual. **"Review AI disagreements"** still applies its filter but no
   longer switches view.
6. **AI toolbar.** Page 1 of `toolbar_stack` / `topbar_action_stack` (AI status,
   Run / Apply AI culling, Sort into folders, Reset, Results) is never shown, and
   the toolbar editor's AI target cannot be edited. AI items placed on the manual
   bar still trigger their actions.
7. **AI-mode chrome** that is built but never visible: `ai_path_combo`,
   `ai_path_control`, `ai_selection_count_label`, and the "AI Review" status label.
8. **Entering-AI side effects** that no longer run: switching sort to `AI_RANK`
   and restoring it on exit, the Smart Groups / Stacks lockout
   (`_apply_ai_review_burst_lockout`), pushing disputed paths into the grid, and
   `_recompute_user_label_bucket_overrides`.
9. **Workflow presets** with `ui_mode="ai"` (`workflows/models.py`) apply in manual.
10. **Plumbing left in place:** the `switch_to_ai_tab` parameter on the AI result
    reload paths is only logged now; `WORKSPACE_TOOLBAR_DEFAULTS["ai"]` and the
    per-mode toolbar layouts still load and save.

## Removed along with the old left rail (not AI, noted for completeness)

The customizable tool rail (pinned toolbar actions with a "+" picker, saved under the
`left_rail` toolbar-layout key) was replaced by the navigation rail. Its code, the
`RAIL_TOOL_*` constants, and the `left_rail_*` display-metric fields are gone. An old
saved `left_rail` entry is simply ignored. Every tool it could pin is still on the
toolbar and in the command palette.
