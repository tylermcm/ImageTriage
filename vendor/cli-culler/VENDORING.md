# Vendored CLI-Culler snapshot

This directory is a **backup-only snapshot** of the CLI-Culler project, vendored
into the image_triage repository so the AI source travels with the app.

## What's here

- `src/aiculler/` — the AI package (ingest, semantic, ranking, adapter, etc.)
- `tests/` — CLI-Culler's own pytest suite
- `categories.csv`, `tag_penalties.csv`, `profiles.csv` — runtime config
- `pyproject.toml` — original package metadata
- `README.md` — upstream CLI-Culler README (unchanged from upstream)

## What's NOT here (deliberately)

- `models/` — ~18 GB of CLIP / TOPIQ weights. Lives outside the repo.
- `.venv/` — CLI-Culler's Python 3.12 virtualenv. Build locally.
- Generated outputs (`banff_*`, `benchmark_*`, `logs/`, `.aiculler_cache/`)
- `.git/`, `__pycache__/`, `.pytest_cache/`

## How image_triage uses it

**image_triage does NOT import from this directory.** At runtime,
`image_triage/aiculler_workflow.py` shells out to the upstream CLI-Culler
checkout via subprocess (see `default_aiculler_runtime()` and the
`IMAGE_TRIAGE_AICULLER_*` env vars). The vendored copy exists so:

1. The AI source is preserved alongside the GUI it powers.
2. The repo on GitHub contains everything needed to understand the system,
   even if the upstream CLI-Culler checkout disappears.
3. Future work can switch to in-process import without first having to find
   the source.

## Re-syncing from upstream

Upstream lives at `C:\Users\tylle\Documents\GitHub\CLI-Culler`. To refresh:

```powershell
$src = "C:\Users\tylle\Documents\GitHub\CLI-Culler"
$dst = "<repo-root>\vendor\cli-culler"
robocopy "$src\src\aiculler" "$dst\src\aiculler" /MIR /XD __pycache__ .pytest_cache /XF *.pyc
robocopy "$src\tests" "$dst\tests" /MIR /XD __pycache__ .pytest_cache /XF *.pyc
Copy-Item "$src\categories.csv","$src\tag_penalties.csv","$src\profiles.csv","$src\pyproject.toml","$src\README.md" -Destination $dst -Force
```
