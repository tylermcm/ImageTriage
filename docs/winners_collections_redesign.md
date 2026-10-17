# Winners and collections redesign — direction

Agreed 2026-10-17. This is the plan of record; build against it, and update it when a decision changes.

## The problem

The collection features work, but there are too many separate ideas to learn: active
collection, the B key, the auto-add toggle, the picker, the popout chip, the right-click
submenu, Check All Visible, drag-and-drop, and Add All Visible. Separately, winners are a
second system that does nearly the same job, plus a `_winners` folder that copies or links
files as a side effect of culling. It has to be definitionally simple.

## Decisions (locked)

1. **Winners are local.** A winner is a judgment about one photo in its shoot, stored with
   that shoot's annotations. The AI culler keeps learning from winner and reject exactly as
   today.
2. **Collections are global.** They're cross-folder, cross-drive sets of references, as now.
3. **Reject stays a global "this photo is bad" mark** and is unaffected.
4. **The `_winners` folder goes away.** Marking a winner never moves, copies or links files.
5. **Getting files out is always an explicit Export action**, never a side effect.

## The design

### 1. W just marks a winner
- W toggles the winner mark (the heart). Nothing on disk changes. Undo works as today.
- The three winner-mode settings (Copy To `_winners`, Link To `_winners`, Annotation Only)
  are removed; behaviour is always "annotation only".

### 2. A built-in "Winners" view across every shoot
- A permanent **Winners** entry at the top of the Collections sidebar, listing every winner
  from every folder.
- It's computed from the winner marks, not stored as a list, so it can't drift and needs no
  maintenance. It replaces the "best of" role the `_winners` folder played, without
  duplicate files.
- It behaves like a collection when you browse it: open, filter, export. Its membership is
  the winner marks, so W and un-W are the only ways in and out.

### 3. "Also add to" — picks can feed a collection
- The top bar shows **Also add to: none ▾**. Choosing a collection makes every W also add
  the photo to it, and un-W remove it, but only if it got there through W, never a hand-added
  photo. This is the behaviour already built for auto-added winners.
- It's always visible, never a hidden mode. It replaces the "active collection" concept and
  the hidden "Winners Also Go to Active Collection" toggle.

### 4. Export (explicit), with PocketDrop
- **Export…** is available on the Winners view, any collection, and the current folder's
  winners. Destinations:
  - **Folder:** copy (or link, where supported) to a folder you choose. This covers what
    `_winners` was used for.
  - **PocketDrop:** a single photo goes in loose. For more than one, zip the photos with the existing archive code
    (`ExportJobsController.create_archive_for_records`), then hand the zip to PocketDrop
    (`PocketDropPanel.add_paths`), so the set is ready to pick up on the phone.
- Export runs as a background job with progress, like the existing archive and handoff jobs.

### 5. Existing `_winners` folders
- They're left on disk untouched; nothing is deleted. After the change they're treated as
  ordinary folders: no special W-deletes-the-copy behaviour, and no special browsing.

### 6. Collection tools stay, but as optional tools
These remain, unchanged or simplified, for people who want them:
- **Add all to collection:** a bar that appears when a filter, search or selection narrows
  the grid ("37 photos · Add all to collection"), replacing the buried menu item.
- Drag photos onto a sidebar collection, or onto "+ New collection".
- Right-click → Collections (ticks, add and remove).
- The popout's collections chip.
- The picker (recent first, type to filter or create), undo, the toast and the sidebar flash.

The Collections menu shrinks to the essentials. Everything else is reached from the places
above.

## What you learn

W marks a winner. Every shoot's winners show up in **Winners**. Want them in a collection too?
Set **Also add to**. Need the files? **Export** (to a folder or PocketDrop).

## Decided after review (2026-10-17)

1. **"Also add to" defaults to none.** Plain culling stays plain.
2. **The B key stays for now** as "add to a collection without marking a winner". It can be
   kept or dropped later at any time.
3. **PocketDrop payload:** more than one file is zipped, and a single file is sent loose.
4. **Winners view scope: everything you've ever marked**, in any folder you've culled in,
   whether or not it's in the Library. It needs no setup. Winners whose files were deleted or
   moved are hidden, not shown as broken.
5. **Winners view is grouped by folder.** Each shoot is a section with a header (folder
   name, parent path, count), newest shoot first, and sections can be collapsed. Clicking a
   header opens that folder. A switch shows the plain flat grid when you want to compare
   across shoots. Exporting, or adding a section to a collection, works per section as well as
   for the whole view.

## Superseded open question

- **Winners view scope:** *the whole library* (every folder with winner marks, including
   ones only opened, not catalogued), or only catalog roots.

## Code map (where the change lands)

- **Winner modes and `_winners` copies:**
  - `models.WinnerMode`, `settings_controller.load_winner_mode` and the settings dialog;
  - `annotation_controller.build_winner_sync_request`, `handle_winner_kept` and
    `WinnerSyncRequest`;
  - `annotation_queue`;
  - `file_ops` (`_winners` destination);
  - `window._is_winners_folder` and its callers (`toggle_winner` deleting a winner copy,
    `record_ops` undo);
  - `ai_run_controller` (`_winners` path at ~line 1340);
  - `apply_ai_decisions_dialog`;
  - `handoff_controller`.
- **Winners view:** read winner marks from `decision_store` (path-keyed, app-wide session)
  and show them as a virtual scope through `records_view.load_virtual_scope_records`, the
  same path collections use.
- **Also add to:** reuse `catalog_controller.winner_auto_add_batch_id`,
  `auto_add_winner_paths` and `auto_remove_unwinner_paths`, driven by a visible top-bar
  selector instead of `ACTIVE_COLLECTION_KEY` / `AUTO_ADD_WINNERS_KEY`. Keep the single-undo
  batching.
- **Export:** `export_jobs_controller` (archive), `handoff_controller` / `pocketdrop.panel`
  (`add_paths`).
- **Collections menu and toolbar:** `ui/actions.py`, `ui/menus.py`, `ui/toolbar_menus.py`,
  `projects_controller`. Remove the active-collection and auto-add actions. Action, menu and
  palette snapshots will need regenerating.
- **AI feedback:** `capture_annotation_feedback` and `sync_annotation_to_global_adapter_label`
  must still fire on W and X exactly as now.

## Build order

1. **W = mark only.** Remove the winner modes and `_winners` side effects, and treat existing
   `_winners` folders as ordinary folders. Verify that AI feedback still fires and undo works.
2. **Winners view** in the sidebar, computed from winner marks.
3. **Also add to** selector in the top bar, replacing the active collection and the auto-add
   toggle.
4. **Export…** to a folder and to PocketDrop.
5. **Simplify the collection tools:** the "Add all" bar in place of the menu item, a slimmer
   Collections menu, and help/docs updated to the one-paragraph model above.

Each step ships on its own with tests, and stays runnable between steps.

## Acceptance

- A first-time user can cull a folder with W and X and find all their picks under
  **Winners** without opening a menu.
- Pressing W never creates, copies or links a file.
- Choosing a collection in "Also add to" and pressing W puts the photo there; one Ctrl+Z takes
  back both the mark and the add.
- Export → PocketDrop puts one zip of the set into PocketDrop.
- The AI culler's training signal from W/X is unchanged (verify against the existing AI
  feedback tests).
