# Product Roadmap

## Purpose

This roadmap translates the UI overhaul direction into an implementation sequence that improves `Image Triage` in the order most likely to increase perceived quality, daily usefulness, and sellability.

Principles for sequencing:

- user-visible polish should arrive early
- architecture should unlock later features instead of blocking them
- workflow speed must not regress
- new systems should be modular, dockable, and keyboard-friendly

## Current State

Already in place:

- professionalized shell foundation
- theme system with `Dark`, `Midnight`, `Light`, and `Auto`
- shared menu and toolbar structure
- stronger file format support including broad RAW coverage, PSD/PSB, AVIF/HEIC-class fallbacks, and STL thumbnails

Still missing:

- true dockable workspace system
- advanced search and metadata filtering
- command palette and full keyboard-first command layer
- XMP/ecosystem compatibility
- higher-end review tooling
- productization features like recovery, diagnostics, and packaging polish

## Priority Order

1. Workspace system and docking
2. Metadata search and advanced filters
3. Command palette and keyboard-first actions
4. XMP and ecosystem compatibility
5. Performance and responsiveness polish
6. Pro review tools
7. Collections and project organization
8. Import/export workflow system
9. Productization and commercial readiness

## Phase 2: Workspace System

Why this comes next:

- biggest immediate jump in professional feel
- directly supports the modular vision
- unlocks inspector, activity, and AI side panels later

Primary deliverables:

- convert the fixed library panel into a `QDockWidget`
- support docking left, right, and bottom
- add at least one new dock scaffold: `Inspector`
- add optional `Activity` or `AI Queue` dock scaffold
- persist dock positions and visibility
- add workspace presets such as `Culling`, `Review`, and `Compare`
- add `Reset Layout` and `Restore Default Workspace`

Architecture tasks:

- add `image_triage/ui/docks.py`
- add `image_triage/ui/layout_state.py`
- split library panel construction out of `window.py`
- centralize dock creation and restore logic

Acceptance criteria:

- user can move the library panel and relaunch without losing layout
- layout reset works reliably
- window menu exposes dock visibility
- shell feels modular rather than fixed

Complexity:

- medium

Sellability impact:

- very high

## Phase 3: Search And Filters

Why this comes after docking:

- this is the biggest workflow usefulness upgrade left
- it turns the app from a viewer into a real review tool

Primary deliverables:

- filename search
- file-type filters: RAW, JPEG, edited, PSD, TIFF, STL, other
- review-state filters: accepted, rejected, unreviewed
- AI-state filters: top picks, grouped, reviewed, pending
- metadata filters: camera, lens, ISO, focal length, date, orientation
- rating and tag filters
- saved searches or smart filters

Architecture tasks:

- introduce a query/filter model instead of only enum-based view filters
- add metadata-backed indexing cache
- add reusable filter widgets for toolbar, dock, and command palette use

Acceptance criteria:

- users can combine filters without losing responsiveness
- search/filter state is visible and reversible
- common review tasks take fewer clicks than they do now

Complexity:

- medium-high

Sellability impact:

- very high

## Phase 4: Command Layer

Why it matters:

- makes the app feel modern and fast
- reduces dependency on visible buttons
- reinforces the professional desktop-app model

Primary deliverables:

- `Ctrl+K` command palette
- searchable action registry
- richer shortcut coverage
- command aliases and fuzzy matching
- recent commands and context-aware actions

Architecture tasks:

- expose commands in a structured registry
- add a palette dialog with command metadata
- unify menu, toolbar, palette, and context action routing

Acceptance criteria:

- every major action can be triggered without hunting the UI
- palette works in both main window and preview contexts

Complexity:

- medium

Sellability impact:

- high

## Phase 5: Interop And Review Tools

Why this phase matters:

- bridges the gap between a strong custom tool and a professional photography workflow product

Primary deliverables:

- XMP sidecar read/write
- ratings, labels, and tags compatible with common photo workflows where practical
- better before/after controls
- survey and compare improvements
- synchronized zoom and pan for compare
- focus-assist or detail-inspection tools
- duplicate and near-duplicate grouping

Acceptance criteria:

- edits and review decisions can travel more cleanly to adjacent tools
- compare workflows feel intentionally designed, not just functional

Complexity:

- high

Sellability impact:

- very high

## Phase 6: Performance And Productization

Why this is a dedicated phase:

- polish is what makes a tool feel expensive
- commercial readiness depends on stability as much as features

Primary deliverables:

- smarter background preloading
- faster cache warm-up and folder switching
- progressive preview quality tiers
- crash recovery and session restore
- diagnostics and non-blocking error reporting
- stronger preferences organization
- packaging/install polish
- updater and licensing-ready settings architecture

Acceptance criteria:

- the app feels resilient under large workloads
- failures are legible and recoverable
- startup, switching, and previewing feel intentional and smooth

Complexity:

- medium-high

Sellability impact:

- high

## Longer-Term Expansion

After the phases above, the best expansion tracks are:

- collections and project organization
- import/export and delivery recipes
- automation hooks and plugin-style extension points
- collaboration or team review features if the product ever moves upmarket

## Recommended Immediate Next Build

Start with `Phase 2: Workspace System`.

Suggested first implementation slice:

1. convert the library panel into a real dock
2. add `Window` menu toggles for docks
3. persist `saveState()` and `restoreState()` for docks
4. add an `Inspector` placeholder dock
5. add `Reset Layout`

This is the most visible and least ambiguous next milestone, and it sets up nearly everything after it.

## Definition Of Success

The roadmap is working if each completed phase makes users say one of these things:

- "This feels like a real professional app."
- "This is faster than the tools I normally use for culling."
- "I can shape the workspace around how I work."
- "I do not have to fight the interface."
