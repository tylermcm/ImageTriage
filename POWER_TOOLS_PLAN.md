# Power Tools Plan

## Purpose

This plan defines a focused "power tools" expansion for `Image Triage`.

The goal is to borrow the best workflow-minded parts of tools like IrfanView without turning the app into a general-purpose image editor. The app should remain primarily about fast browsing, culling, review, and practical file operations around image sets.

## Product Position

`Image Triage` should become:

- a fast image triage application
- a reliable file-workbench for photo folders
- a practical batch utility for rename, move, resize, and conversion

It should not become:

- a layer-based editor
- a RAW development application
- a Photoshop replacement
- a metadata-management suite on the scale of Lightroom

## Scope Rules

Every new utility should pass this test:

- it supports triage, review, or delivery workflows
- it works well on whole folders or multi-selection
- it is safe and previewable
- it fits naturally into context menus, the command palette, or compact dialogs

If a feature requires a deep editing canvas or a large, separate workflow model, it is probably out of scope.

## Experience Goals

The new functionality should make users say:

- "I can stay in this app longer before I need Explorer."
- "I can clean up folders and filenames without leaving review mode."
- "I can prepare output copies without opening Paint or Photoshop."
- "Batch tasks feel safe because I can preview what will happen."

## Feature Pillars

## 1. File And Folder Operations

These are the most natural near-term additions because the app already has move, copy, delete, restore, and folder rename foundations.

Primary additions:

- rename selected file
- rename focused file from preview
- create folder from the tree, favorites area, or current folder
- delete folder with guardrails
- move folder
- create subfolder from selection
- move selection into new folder
- move selection to recent destinations
- duplicate file or duplicate bundle

UX notes:

- single-file rename should be inline where practical and dialog-based where clearer
- folder creation should default to the current folder or selected tree node
- destructive folder actions should explain whether the folder is empty
- recent destinations should reduce repeated file-dialog friction

## 2. Batch Rename Studio

This is the highest-value "power user" feature after core file operations.

The rename flow should be UI-first rather than regex-first.

Rule types:

- replace text
- add prefix
- add suffix
- trim from start or end
- numbering
- case conversion
- collapse whitespace
- remove invalid filename characters
- extension normalization
- date tokens
- sequence tokens
- optional advanced regex mode

Required behavior:

- live before and after preview
- collision detection before commit
- skip or overwrite strategy selection
- preserve extensions by default
- optional extension edits when the user explicitly enables them
- folder-scoped or selection-scoped operation modes

Nice additions:

- save rename presets
- reusable recipes like "client delivery", "raw sequence", and "web export"
- sortable preview list

## 3. Resize And Convert Studio

This should behave more like a practical export utility than an editor.

Primary capabilities:

- resize selected images
- resize current folder
- optional recursive folder processing
- convert copies between common output formats
- preserve originals by default
- choose output folder or sibling export folder

Output targets to support first:

- JPG
- PNG
- WEBP
- TIFF

Source handling:

- accept the app's supported readable formats as inputs where possible
- treat RAW, PSD, HEIC, and other specialized inputs as source formats
- do not promise full-fidelity editing or round-trip preservation

Resize options:

- fit within width and height
- width only
- height only
- longest edge
- percentage scaling
- no upscaling toggle

Conversion options:

- format
- quality for lossy outputs
- background fill for alpha-to-non-alpha conversion
- output naming suffix
- overwrite, skip, or make-unique behavior

Preset ideas:

- 2048px JPG
- web review JPG
- PNG copy
- thumbnail set

## 4. Drag And Drop Workflow

This is one of the best "it feels like a real desktop app" upgrades.

Target behaviors:

- drag selected thumbnails onto a folder in the tree to move
- hold a modifier to copy instead of move
- drag onto favorites or a recent destination target
- accept folders dropped from Explorer to open them

Future-friendly but not first priority:

- dragging files back out to Explorer
- cross-window drag between multiple app windows

Implementation note:

Internal drag and drop should reuse the same move or copy service used by menus and dialogs so behavior stays consistent.

## 5. Context Menus And Command Surface

The new tools should not live in one giant dialog alone. They should appear where users already work.

Additions for the grid context menu:

- rename
- batch rename selected
- resize selected
- convert selected
- duplicate
- move to recent folder
- create new folder from selection

Additions for folder tree and favorites context menus:

- new folder
- delete folder
- move folder
- batch rename files in folder
- resize or convert folder

Additions for the command palette:

- rename current file
- new folder here
- batch rename current folder
- resize selection
- convert selection
- move to recent destination

## What We Should Explicitly Avoid For Now

Do not expand into these areas during the first power-tools wave:

- crop, paint, draw, text overlays, and retouching
- histogram or levels editing
- color correction workflows
- layer handling
- slideshow authoring
- print layout features
- full metadata editing beyond what already supports review state
- exotic conversion targets that introduce fragile dependencies

## Architecture Direction

The current window already contains useful bundle-aware file logic. The next step should be to extract and reuse it rather than duplicate more code in `window.py`.

Recommended modules:

- `image_triage/file_ops.py`
- `image_triage/batch_rename.py`
- `image_triage/image_transforms.py`
- `image_triage/ui/rename_dialog.py`
- `image_triage/ui/batch_rename_dialog.py`
- `image_triage/ui/transform_dialog.py`

Core responsibilities:

- `file_ops.py`: shared rename, move, copy, duplicate, folder creation, folder deletion, collision strategies, undo payloads
- `batch_rename.py`: rename-rule model, preview generation, validation, collision reporting
- `image_transforms.py`: load, resize, convert, encode, and export operations
- UI dialogs: preview-first workflows for batch tasks

Important design rule:

File operations must remain bundle-aware. If a record includes sidecars, edited companions, or stack-related assets, the operation should know whether it is acting on the visible file only or the full bundle.

Suggested action modes:

- visible file only
- primary file plus sidecars
- full bundle

## Safety Model

These utilities should feel powerful, not dangerous.

Required safeguards:

- preview before batch rename
- preview summary before batch resize or convert
- explicit collision policy
- explicit output destination
- undo for rename and move where practical
- safe default of creating copies for resize and convert
- confirmation for destructive folder operations

Collision policies:

- skip existing
- overwrite existing
- make unique
- stop on first collision

Rollback expectations:

- rename and move operations should be transactional where possible
- batch operations should either fully apply or return a clear partial-failure report

## Delivery Phases

## Phase 1. File Ops Foundation

Goal:

Extract reusable file and folder operations and add the most obvious missing actions.

Deliverables:

- single-file rename
- create folder
- delete folder
- move selection into new folder
- move to recent destination
- shared file-ops service

Why first:

- unlocks nearly everything else
- immediately improves context menus
- keeps later batch utilities from becoming one-off code

## Phase 2. Batch Rename Studio

Goal:

Add the most valuable high-volume utility workflow.

Deliverables:

- rename-rule model
- preview table
- collision handling
- presets
- selection and folder scopes

Why second:

- large user value
- highly visible differentiator
- benefits directly from Phase 1 service extraction

## Phase 3. Resize And Convert Studio

Goal:

Let users generate delivery-ready copies without leaving the app.

Deliverables:

- resize presets
- format conversion
- output naming and destination controls
- selection and folder scopes

Why third:

- broad usefulness
- strong complement to culling and delivery workflows
- manageable once shared batch and preview patterns exist

## Phase 4. Drag And Drop

Goal:

Make file movement feel native and fast.

Deliverables:

- grid-to-folder-tree drag move
- modifier-to-copy support
- drop folder from Explorer to open

Why fourth:

- strong polish impact
- easier once shared move and copy services already exist

## Recommended Immediate Build

Start with `Phase 1. File Ops Foundation`.

Suggested first implementation slice:

1. extract shared bundle-aware rename and move helpers from `window.py`
2. add single-file rename to the grid context menu and preview command set
3. add `New Folder...` to folder tree and current-folder actions
4. add `Move Selection To New Folder...`
5. add recent destinations for move and copy

This gives the app immediate day-to-day value while establishing the service layer the later tools need.

## Acceptance Criteria

The power-tools expansion is succeeding if:

- users can rename, move, and organize files without leaving the app
- batch rename operations are previewable and safe
- resize and convert behave like export tools, not risky destructive edits
- drag and drop feels native and reuses the same underlying action rules
- the app becomes more multifaceted without feeling unfocused

## Definition Of Done For The First Wave

The first wave is complete when the app can comfortably handle:

- folder cleanup
- filename cleanup
- folder creation and routing
- selection-based move workflows
- basic delivery copy generation

At that point, the app will have crossed from "review utility" into "review utility with real operational depth" without losing its identity.
