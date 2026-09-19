# Collections Now, Projects Later

Status: product direction agreed on 2026-09-13.

## Decision

The existing feature is a **Collection**, not a Project.

It will be presented honestly as Collections throughout the application. Collections remain persistent, non-destructive sets of image-bundle references that may span any number of folders. This is useful when someone wants to preserve winners, portfolio candidates, edit candidates, proofing selections, or another intentional set assembled from different locations.

The application will not expand the current collection implementation into a feature-heavy Projects system merely by adding workspace memory, progress bars, stages, dashboards, deadlines, or other project-management chrome. A future Projects feature remains possible, but only after research identifies a valuable photographic workflow that folders, sessions, and Collections cannot already handle well.

## Why we changed direction

A normal shoot already has an effective workspace: its folder.

Opening that folder in Image Triage already provides its images, bundles, ratings, culling decisions, AI analysis, filters, editing, comparison, and output commands. Remembering its last image, scroll position, sorting, filters, and review progress would improve the folder/session experience, but it would not make a duplicate Project abstraction valuable.

The previous Project proposals also required users to manually add hundreds or thousands of photographs to a Project before doing substantially the same work they could already perform in the source folder. That is additional administration rather than simplification.

Manual membership is appropriate when selection itself is the work, such as choosing twenty portfolio images from several folders. It is inappropriate for representing an entire shoot, folder, card, edit queue, or rule-derived result.

## Product boundaries

### Folders

Folders answer: **Where do the photographs live?**

They remain the default surface for ordinary ingest, culling, review, editing, and file operations.

### Sessions

Sessions answer: **Where was I in this review?**

Resume position, scroll position, sorting, filters, grid configuration, and review progress should be available automatically for folder work. A user should not need to create a Project to receive basic continuity.

### Collections

Collections answer: **Which photographs do I want to keep together?**

They are:

- Explicit and lightweight.
- Cross-folder.
- Bundle-aware.
- Ordered when useful.
- Reference-only and non-destructive.
- Appropriate for relatively deliberate selections.
- Never a requirement for normal folder-based work.

Adding an image to a Collection never moves or copies the source. Removing an image from a Collection never rejects or deletes it. Deleting a Collection deletes only the saved references.

### Future Projects

A future Project must answer a different question that cannot be answered adequately by a folder, resumable session, or Collection.

It must not be defined as any of the following:

- A folder with memory.
- A Collection with more metadata.
- A second copy of the culling grid.
- A manually populated container for an entire shoot.
- A dashboard that reports information without simplifying an action.
- A generic task manager for photographers.

## Value test for a future Project

Every proposed Project capability must answer all of these questions:

1. What recurring user problem does it eliminate?
2. Why can the folder, session, or Collection not solve that problem more simply?
3. Which concrete steps or decisions does it remove?
4. How is its content populated without manually selecting thousands of files?
5. What action becomes safer, faster, or possible for the first time?
6. Does the benefit recur often enough to justify permanent UI and data-model complexity?

If the proposal cannot answer those questions, it should not become a Project feature.

## Non-negotiable technical and product constraints

- Image Triage is local, Windows-focused, folder-first, and non-destructive.
- Original files and normal filesystem locations remain the source of truth.
- Related RAW, JPEG, and edited variants remain one logical image bundle.
- Global ratings, rejects, winners, tags, edits, and AI results remain properties of the underlying image. A Project must not silently create competing versions of them.
- Cross-folder behavior must remain possible.
- Memory cards, external drives, and network locations may be temporary or unavailable.
- No feature may silently move, copy, rename, reject, remove, or delete source photographs.
- Expensive scans, hashing, resolution, and indexing must not block the UI.
- The existing grid, viewer, inspector, editor, filters, AI tools, and output tools should be reused rather than cloned.
- Creating a Project cannot be a prerequisite for normal culling.
- Bulk membership must be automatic or source/rule driven. Manual addition is acceptable only when deliberate curation is the point.
- A future Project must provide user value beyond saved workspace state; ordinary folder sessions should remember their own state.

## Current Collection scope

The terminology change does not replace or migrate the existing collection database. Existing saved entries remain intact.

Collections continue to support:

- Creating a named set from the current selection.
- Storing image-bundle references from multiple folders.
- Reopening those images together.
- Adding and removing selections.
- Preserving membership without moving or duplicating files.
- Deleting the Collection without deleting photographs.

Future Collection improvements may include easier Add to Collection actions, reliable context-menu targeting, deliberate ordering, and smart or rule-derived membership. Those improvements do not imply that Collections should become Projects.

## Deep-research prompt

Copy everything below into a deep-research task.

---

Conduct evidence-based product research for a possible future **Projects** feature in a local Windows photo-culling application called **Image Triage**. Do not assume that a Projects feature should exist. “Do not build Projects” is an acceptable conclusion if the research cannot identify a frequent and valuable job that other parts of the application cannot solve more simply.

### Existing product model

Image Triage is a fast, folder-first, non-destructive desktop application. A user can open a real filesystem folder or camera card and immediately browse image bundles, cull, rate, reject, compare, filter, run AI analysis, edit, and export. Related RAW, JPEG, and edited variants are treated as one logical image bundle. Original files remain ordinary files in their existing locations.

The intended conceptual boundaries are:

- **Folders:** where photographs physically live and where ordinary culling happens.
- **Sessions:** automatic continuity for folder work, including resume position, filters, sorting, grid state, and review progress.
- **Collections:** lightweight, persistent, non-destructive sets of selected image bundles. Collections may span folders and are useful for winners, portfolio candidates, edit candidates, proofing selections, themes, or other deliberately curated sets.
- **Projects:** currently undefined. Research must determine whether there is a separate, defensible user job for them.

The previous implementation called Collections “Projects.” It merely stored a name, purpose, description, and an ordered list of selected file paths. Opening one displayed those images in the normal grid. This was rejected as a meaningful Projects concept because it was little more than a saved filter. Proposed additions such as resume state, progress bars, workflow stages, covers, deadlines, and dashboards also failed to establish unique value: much of that belongs to ordinary folder sessions, while the rest risks adding user maintenance without simplifying work.

### Central problem to investigate

A photographer who offloads a shoot to a folder can already open that folder and perform the entire Image Triage workflow. Requiring that photographer to manually add hundreds or thousands of the same images to a Project before repeating the folder workflow is clearly worse.

Identify whether photographers have important, recurring workflows that:

1. Outlive or cross the boundaries of one folder/session.
2. Cannot be solved more simply by improving folders, automatic session memory, saved filters, Collections, or export presets.
3. Would materially reduce repeated work, uncertainty, mistakes, or cognitive load.
4. Can be populated automatically from sources, rules, existing decisions, exports, or other events rather than requiring manual bulk membership.
5. Fit naturally inside a fast photo-culling application instead of turning it into a digital asset manager, project-management suite, or client portal.

### Required research

Research real workflows rather than brainstorming features in isolation. Use current, credible sources from professional photographers, studios, photography workflow educators, culling/editing software documentation, proofing/delivery products, and relevant user discussions. Separate documented evidence from inference.

Investigate at least these user segments:

- Wedding and event photographers.
- Portrait and school photographers.
- Sports and volume photographers.
- News, editorial, and agency photographers.
- Studio/product photographers.
- Fine-art, portfolio, book, and exhibition photographers.
- Serious hobbyists managing long-lived personal libraries.
- Photographers who cull directly from cards or external drives.

For each segment, map the real workflow from capture through ingest, culling, editing, review, export, delivery, revision, and archival. Identify where users leave their culling application for spreadsheets, notes, file naming conventions, duplicate folders, external services, or manual memory. Determine whether those transitions represent opportunities for Image Triage or responsibilities that should remain in specialized software.

Compare the relevant behavior of tools such as Photo Mechanic, Lightroom Classic, Capture One, Bridge, Narrative Select, AfterShoot, FastRawViewer, Photo Supreme, Mylio, digiKam, proofing/delivery services, and any other products directly relevant to the discovered workflows. Do not create a generic feature checklist. Explain which user problem each competing capability actually solves and how frequently it is likely to matter.

### Constraints

- Do not propose “folder plus saved UI state” as a Project. Folder sessions should already remember their state.
- Do not propose manually adding an entire shoot’s hundreds or thousands of photographs to a Project.
- Do not duplicate global ratings, rejects, winners, tags, edits, or AI results into Project-specific versions.
- Do not require import into a proprietary library or move original files.
- Do not require cloud accounts, collaboration, or a client portal.
- Do not make Projects mandatory for ordinary culling.
- Do not justify a feature merely because it can display additional metadata.
- Do not use dashboards, stages, deadlines, notes, covers, or activity logs unless each one directly eliminates a demonstrated workflow problem.
- Do not silently perform destructive file actions or make AI decisions on the user’s behalf.
- Account for temporary cards, changing drive letters, external drives, network paths, missing files, and bundle companions.
- Any expensive indexing, hashing, or path resolution must be asynchronous and incremental.

### Questions the final answer must resolve

1. Is there sufficient evidence that Image Triage should have a separate Projects feature?
2. If yes, what is the single clearest job-to-be-done that defines it?
3. Which user segment experiences that problem most frequently and severely?
4. What does a Project enable that a folder, session, Collection, saved filter, or export preset cannot?
5. What information would a Project own, and why must that information exist?
6. How would a Project be created and populated without manual bulk loading?
7. What is the shortest end-to-end user flow, and exactly which existing steps does it remove?
8. What should remain outside Image Triage?
9. What are the strongest alternative solutions that do not require Projects?
10. What evidence would falsify the recommendation?

### Required output

Provide:

1. **Executive verdict:** Build, defer, replace with a smaller concept, or do not build.
2. **Evidence summary:** Sourced observations separated from product inference.
3. **Workflow pain ranking:** Frequency, severity, current workaround, and affected segment.
4. **Alternatives analysis:** Folder/session improvements, Collections, saved filters, export presets, or other smaller solutions.
5. **One recommended concept at most:** Do not present several equally weighted feature concepts. If no concept clears the value threshold, say so.
6. **Concrete before-and-after workflow:** Count the steps removed and identify reduced risks.
7. **Automatic population model:** Explain how content enters and leaves without bulk manual work.
8. **Strict ownership model:** Separate global image state, Collection state, and any truly necessary Project state.
9. **Minimum valuable product:** The smallest implementation that proves the user value; omit decorative features.
10. **Kill criteria:** Conditions under which development should stop or the concept should collapse into Collections or sessions.
11. **Validation plan:** Interviews, workflow observation, prototype tests, and measurable success criteria.
12. **Sources:** Direct links and publication dates wherever available.

The standard of proof is not “this sounds useful.” The recommendation must demonstrate that a future Project removes meaningful work that photographers currently perform repeatedly and that cannot be removed more cleanly by improving folders, sessions, Collections, or outputs.

---
