# UI Overhaul Plan

## Product Goal

Turn `Image Triage` from a capable utility into a professional desktop application that feels coherent, sellable, and customizable without sacrificing speed.

The UI should feel closer to a polished creative tool than a prototype:

- command-driven instead of button-driven
- modular instead of fixed
- content-first instead of chrome-first
- consistent across the main window and preview
- themeable, with dark, light, and auto appearance

## What We Should Preserve

These are already strengths and should survive the overhaul:

- the thumbnail viewport and browsing workflow
- the folder explorer and favorites concept
- keyboard-heavy review flow
- fast folder loading, AI helpers, and preview performance

The redesign should improve the shell around those features, not replace the core workflow.

## Current Problems

The existing UI works, but it reads as handmade rather than productized:

- top-level controls are rows of unrelated text buttons
- actions are duplicated across buttons, menus, and preview without a shared system
- there is no true desktop-app menu structure
- there is no docking or saved workspace layout
- styles are scattered across widgets instead of driven by a theme system
- the preview header looks separate from the main app rather than part of the same product
- the custom grid and preview both hard-code colors, which blocks real dark/light support

## North Star

The target shell should feel like a lightweight photo-review application with the clarity of VS Code and the panel flexibility of Photoshop or Lightroom.

That means:

- a real menu bar
- icon-led toolbars with strong tooltips
- dockable panels
- remembered layouts
- clean, restrained visual styling
- denser, smarter control grouping
- fewer visible buttons, but better discoverability

## Design Principles

### 1. Command First

Every important action should be defined once as an application command and surfaced in several places:

- menu bar
- toolbar
- keyboard shortcut
- context menu
- preview controls

This prevents the UI from feeling random and keeps behavior consistent.

### 2. Content First

The grid and preview are the product. The shell should support them, not compete with them.

- keep chrome slim
- reduce heavy button rows
- prioritize image area and scanability
- use stronger hierarchy instead of more controls

### 3. Modular Workspace

Panels should be movable and stateful.

- explorer can live left, right, or bottom
- future inspector panels can be shown or hidden
- the app remembers the last workspace layout
- a reset layout command restores the default

### 4. Calm Visual Language

The UI should look serious and premium, not flashy:

- neutral base surfaces
- one deliberate accent color
- quiet borders
- strong spacing rhythm
- consistent iconography
- obvious hover, active, disabled, and selected states

### 5. Theme Fidelity

Dark, light, and auto modes should all feel intentional.

This requires a real token system, not a global stylesheet swap.

## Target Information Architecture

## Main Window Shell

The main window should be organized like this:

1. Menu bar
2. Primary toolbar
3. Optional context toolbar or mode strip
4. Dockable workspace around the central viewport
5. Status bar

The current summary strip can stay, but it should become more compact and integrated with the shell language.

## Menu Bar

Recommended top-level menus:

- `File`
- `Edit`
- `View`
- `Review`
- `AI`
- `Window`
- `Help`

### File

- Open Folder
- Refresh Folder
- Open Recent
- Add Current Folder To Favorites
- Rename Current Folder
- Workflow Settings
- Exit

### Edit

- Undo
- Cut
- Copy
- Delete
- Move To Folder
- Move To `_keep`
- Select All
- Clear Selection

### View

- Appearance: Dark / Light / Auto
- Layout presets
- Reset Layout
- Toggle panel visibility
- View filters
- Column count
- Compare mode
- Auto-advance

### Review

- Accept
- Reject
- Restore
- Rating submenu
- Tags
- Open Preview
- Reveal In Explorer
- Open In Photoshop

### AI

- Run AI Culling
- Load Saved AI For Folder
- Load AI Results
- Clear AI Results
- Open AI Report
- Next AI Top Pick
- Next Unreviewed AI Top Pick
- Jump To AI Top Pick In Group
- Compare Current AI Group
- AI-specific filters

### Window

- Show Library
- Show Inspector
- Show AI Review
- Show Activity
- Reset Window Layout
- Full Screen

### Help

- Keyboard Shortcuts
- Review Workflow Help
- AI Workflow Help
- About

## Toolbar Strategy

The top toolbar should stop trying to show every command at once.

### Primary Toolbar

This stays visible in all modes and uses icons first, text second where useful.

Suggested groups:

- file: open, refresh
- edit: undo
- review: accept, reject, keep, delete
- view: compare, auto-advance
- AI: run/load/report
- utility: appearance, layout, help

Notes:

- use `QAction` and `QToolBar`
- use `QToolButton` for compact command surfaces
- use icons with tooltips and shortcut hints
- use separators and grouped spacing
- avoid long text labels unless the action is rare or high-risk

### Mode Strip

Replace the current plain `QTabBar` with a cleaner mode switch:

- `Manual Review`
- `AI Review`

This can be a compact segmented control style widget or styled tab bar.

## Dockable Panels

## Default Workspace

Default arrangement:

- left dock: `Library`
- center: viewport
- optional right dock: `Inspector`
- optional bottom dock: `Activity` or `AI Review`

## Panel Definitions

### Library Dock

Contains:

- favorites
- folder explorer

This replaces the fixed left panel and becomes a `QDockWidget`.

### Inspector Dock

Phase 2 target.

Contains contextual info for the current selection:

- file name and path
- capture metadata
- review state
- AI score and group details when present
- quick actions

This lets us remove some clutter from the top shell.

### AI Review Dock

Phase 3 target.

Potential contents:

- current AI run status
- group summary
- next-pick shortcuts
- review queue hints

### Activity Dock

Optional future panel for:

- scan progress
- AI run stages
- non-blocking feedback and logs

## Theming System

## Appearance Modes

- `Dark`
- `Light`
- `Auto`

`Auto` should follow the system preference when possible and otherwise use a stored fallback.

## Theme Tokens

Create one shared theme model for the shell and custom widgets.

Core token categories:

- window background
- panel background
- control background
- raised surface
- border
- muted border
- primary text
- secondary text
- disabled text
- accent
- accent hover
- success
- warning
- danger
- selection fill
- selection outline

The custom grid and preview should consume these tokens instead of local color constants.

## Iconography

Use icons instead of text-only buttons for high-frequency actions.

Implementation order:

1. use Qt standard icons where possible
2. introduce a consistent SVG icon set for product polish
3. tint icons from theme tokens so dark/light mode remains clean

## Typography And Spacing

Use a restrained desktop scale:

- clear title hierarchy
- compact but readable labels
- consistent control heights
- predictable padding and spacing units

The goal is to look intentional and dense, not oversized.

## Preview Redesign

The preview should feel like part of the same product, not a separate tool window.

## Preview Header

Replace the current wide button row with grouped controls:

- navigation and compare group
- compare count and mode controls
- edit-source controls
- external app actions

Use consistent buttons, icons, and toggles that match the main toolbar language.

## Preview Layout

Recommended structure:

- slim header
- dominant image area
- clean metadata/footer rail

Future-friendly option:

- collapsible metadata sidebar instead of forcing everything into the footer

## Preview Behavior Goals

- match shell theming
- reduce visual noise
- make compare mode easier to read
- make edit/original state more obvious
- keep keyboard behavior unchanged

## Architecture Refactor

The UI overhaul should not stay inside the current `window.py` monolith.

Recommended new module structure:

- `image_triage/ui/theme.py`
- `image_triage/ui/actions.py`
- `image_triage/ui/icons.py`
- `image_triage/ui/menus.py`
- `image_triage/ui/toolbars.py`
- `image_triage/ui/docks.py`
- `image_triage/ui/layout_state.py`
- `image_triage/ui/widgets/...`

Suggested responsibilities:

- `actions.py`: define and wire shared `QAction` objects
- `theme.py`: appearance mode, tokens, palette, stylesheet generation
- `menus.py`: build menu bar from shared actions
- `toolbars.py`: build top toolbar(s) from shared actions
- `docks.py`: create library, inspector, and activity docks
- `layout_state.py`: persist and restore dock/window state

`window.py` should become an orchestrator, not the home of every widget and command.

## Persistence

Persist these UI choices with `QSettings`:

- appearance mode
- dock visibility
- dock positions
- toolbar visibility
- last workspace preset
- window geometry
- main window state

Use `saveGeometry()` / `restoreGeometry()` and `saveState()` / `restoreState()` once the shell moves to dock widgets.

## Implementation Phases

## Phase 1: Shell Foundation

Goal:

Introduce the architecture needed for a professional shell without changing the core workflow.

Deliverables:

- shared action registry
- menu bar
- primary toolbar
- appearance manager
- saved window geometry and layout state
- default shell styling

Non-goals:

- no big workflow changes
- no deep preview rewrite yet
- no AI behavior changes

## Phase 2: Docking And Workspace

Goal:

Make the app modular.

Deliverables:

- convert fixed explorer panel into `Library` dock
- add dock visibility controls
- add reset layout
- add at least one additional dock placeholder or inspector
- ensure layout persistence works reliably

## Phase 3: Preview Redesign

Goal:

Bring the preview shell up to the same standard as the main app.

Deliverables:

- grouped preview controls
- shared command usage
- consistent icons and toggles
- cleaner metadata presentation
- theme-aware preview surfaces

## Phase 4: Theme Completion

Goal:

Make dark, light, and auto truly complete.

Deliverables:

- move grid colors to theme tokens
- move preview colors to theme tokens
- move dialog colors to theme tokens
- verify contrast and readability in both themes

## Phase 5: Product Polish

Goal:

Make the app feel shippable.

Deliverables:

- refined icons
- layout presets
- stronger empty states
- better status feedback
- cleaner dialogs
- final consistency pass across shell, preview, and menus

## Acceptance Criteria

The overhaul is successful when:

- the app looks coherent before the user clicks anything
- menus expose the main workflow clearly
- top-level buttons are no longer cluttered or redundant
- the explorer can be docked left, right, or bottom
- the layout is remembered between launches
- dark, light, and auto all work without unreadable controls
- preview and main window feel like one product
- keyboard-heavy users are not slowed down

## First Implementation Milestone

The first build step should be:

1. create a shared action system
2. add a real menu bar
3. add a compact primary toolbar
4. move appearance and layout controls into commands
5. keep the existing splitter-based center layout temporarily

That gives us an immediate professionalism boost before the dock refactor.

## Deferred Ideas

Good ideas, but not phase-1 priorities:

- command palette
- customizable keyboard shortcuts UI
- workspace presets for culling vs AI review
- detachable preview as a persistent panel
- filmstrip mode
- multi-monitor workspace presets

## Recommendation

Build the overhaul in this order:

1. shell foundation
2. docking and layout persistence
3. preview redesign
4. full theme rollout
5. polish pass

This keeps the app stable while still delivering visible quality improvements early.
