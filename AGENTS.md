# Image Triage development

## PhotoCraft integration: session updates

PhotoCraft is actively developed. At the start of every development session in
this repository, inspect the companion `../photocraft` checkout, fetch both its
upstream (`origin`, storytold/photocraft) and user fork (`fork`, tylermcm/photocraft),
and compare their commits with our integration branch. A failed fetch must be
reported; do not describe an offline checkout as current.

Merge upstream on a release, not on every session. Merging each upstream push
turned out to be a constant uphill battle; one merge per release is easier. Unless
the user says otherwise, merge only when upstream cuts a release (a new version tag).

Every session still checks, so a large conflict is seen before the release: dry-run
the merge without starting it (`git merge-tree --write-tree --name-only HEAD
origin/main`), and report how far upstream has moved, which files would conflict
(Camera Raw ones in particular) and whether the conflicts look large. If they are
large, say so and ask whether to merge early; do not merge early on your own.

Image Triage's own work lives on `main` (its integration branch was retired);
`../photocraft` keeps its host changes on `codex/image-triage-integration`. Preserve
user edits and existing commits before integrating updates. Also examine any
fork-only work and integrate it when it belongs to that branch.

## Commits and pushes

You may commit and push without asking: `main` of this repository to `origin`
(tylermcm/ImageTriage) and, in `../photocraft`, `codex/image-triage-integration` to
`fork` (tylermcm/photocraft). Run the relevant tests first and say what was pushed.
Push normally only: never force-push (`--force`, `--force-with-lease`), never
rewrite commits that were already pushed, never push to storytold, and never create
an upstream pull request.

Commits and pull-request descriptions carry no AI attribution: no `Co-Authored-By`
line (or any trailer naming Claude, an assistant or a model) and no "Generated with"
line. Commits are authored by the configured git user.

Review conflicts individually and review automatic merges in shared integration
files too: desktop startup/services, UI control/jobs/menus, theme tokens, native
window behavior and automation authorization. Record the old/new upstream hashes,
conflicted files, resolution decisions and validation in
`docs/photocraft_handoff.md`.

The fork carries only what hosting PhotoCraft inside Image Triage needs. Where upstream
has built something the fork also built (in this version or a later one), drop the fork's
version and take upstream's; keep ours only for host behaviour upstream does not have.
Decide that per conflict and per automatic merge, not for a whole file at a time unless the
file holds nothing of the host's.

Read the companion's AGENTS.md and apply its validation requirements. Rebuild and
run the Image Triage handoff checks before promoting a new editor build. Preserve
the known-good hosted executable until validation passes. Verify persistent
filmstrip navigation, automatic stash/restore, native Save and grid propagation,
closed-tab reopen, hidden startup, theme/resize integration, fullscreen, shutdown
and process cleanup. A merge without textual conflicts is not proof of compatibility.

See `docs/photocraft_handoff.md` for the protocol and last validated update.
