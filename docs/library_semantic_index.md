# Library-wide semantic index (foundation)

Branch `claude/library-semantic-index`, cut from `codex/image-triage-integration` at `d2d0f08`.
Infrastructure only: no Find Similar / Discover UI yet.

## Why

Semantic (TinyCLIP) search only works per folder, and only for folders that have been
opened, because embedding happens when a folder loads. A search across the library would
silently miss most of it. This branch adds background indexing of every catalog folder and
one library-wide index that a later Find Similar / Discover UI can query.

## Design

```
catalog_records (library.sqlite3)          per-folder feature stores (source of truth)
  folder -> image paths                     <folder>/.image_triage_ai/artifacts/aiculler.sqlite
          |                                            ^                     |
          v                                            | embeds missing      | copy valid vectors
  LibraryIndexController ---- jobs.json ---> aiculler/library_index_worker.py |
  (Qt, GUI thread)                            (separate process, below-normal priority)
          |                                                                  v
          +---- _ReconcileTask (QRunnable) ----> LibrarySemanticStore (library_semantic.sqlite3, derived)
                                                          |
                                                          v
                                              LibraryVectorIndex (NumPy matrix) -> RankedResult
```

- **`image_triage/semantic_plan.py`** (new): the Qt-free half of `semantic_index.py` (model
  identity, file signatures, `plan_semantic_index`). Moved so the worker process can share
  exactly the same planning rules without importing PySide6. `semantic_index` re-exports
  every name, so existing callers are unchanged.
- **`aiculler/library_index_worker.py`** (new): runs in the AI runtime's Python like
  `aiculler/cli.py`. For each folder it plans, adopts full-workflow embeddings, and embeds
  the rest into that folder's own feature store. Control is a file holding `run` / `pause` /
  `stop`, checked between images. Output is `LIBRARY_INDEX {json}` lines. A separate process
  rather than a thread keeps inference out of the GUI process's interpreter, memory and
  thread pools, lets the OS priority be lowered, and lets it be killed cleanly. Its import
  chain is Qt-free (checked).
- **`image_triage/library_semantic.py`** (new):
  - `LibrarySemanticStore`: the derived store. `sync_folder` copies a folder's vectors whose
    recorded file signature still matches the file and whose model identity is current.
    Edited, moved, renamed or deleted files drop out; nothing is re-embedded just to be
    copied. It skips a folder cheaply when neither its feature store's mtime nor its catalog
    file list changed. A folder whose drive is missing is marked unavailable (kept, not
    searched); a folder removed from the catalog is forgotten. A schema change wipes and
    rebuilds, since it's all derived.
  - `LibraryVectorIndex`: all available vectors as one contiguous, L2-normalised float32
    matrix. `search(query, within=, exclude=)` and
    `similar_to(paths, negatives=, negative_weight=0.5, within=)` with
    `q = normalize(mean(P) - w * mean(N))`; the examples themselves are left out of the
    results. Rankings are cached (last 8), and a `RankedResult` can be re-cut at any
    threshold (`cut`, `count_at_least`, `top`) without searching again. Queries never embed,
    stat or open folder stores.
  - `text_query_vector(encoder, text)`: the same caption-template averaging as the
    per-folder search, for typed queries.
  - `Coverage` / `describe_coverage`: indexed vs total photos, overall and per catalog root,
    plus counts on disconnected drives.
- **`image_triage/library_index_controller.py`** (new):
  - Opt-in: nothing runs until **Library > Catalog > Index Library For Search** (also in the
    command palette). From then on it continues whenever the app is idle, across restarts,
    until **Pause Library Indexing**.
  - Idle-yield: an app-wide event filter stamps the time of every key press, click and wheel
    event. The worker is told to pause while you've been active in the last 20 s, or while
    the editor owns the GPU (`_background_indexing_suspended`). It pauses within one image
    and resumes after the quiet spell.
  - The folder that's currently open is skipped, because the app's own per-folder indexer is
    already working on it.
  - Each worker batch is reconciled into the library index (coalesced), so coverage grows as
    it runs. A full pass runs again when the worker finishes.
  - A Library menu line shows e.g. "Library search: 36,000 of 50,000 photos (72%) —
    paused". Choosing it shows a per-root breakdown and explains that semantic search only
    finds indexed photos.
  - If the worker exits with an error, the line shows its last output instead of relaunching
    in a loop.
  - `vector_index()` hands the loaded `LibraryVectorIndex` to the future UI and reloads it
    after vectors change.
- `LibraryStore.catalog_folder_sources()` (new): folder → image paths for enabled roots,
  without decoding records.

## Measurements (this machine, numpy 2.4.6, `benchmarks/library_semantic_search.py`)

| photos  | load index | matrix  | search | cached search | similar (4 + 2 neg) | re-cut |
|---------|-----------:|--------:|-------:|--------------:|--------------------:|-------:|
| 10,000  | 173 ms     | 20 MB   | 2.6 ms | 0.01 ms       | 2.8 ms              | 0.05 ms |
| 50,000  | 978 ms     | 102 MB  | 9.4 ms | 0.01 ms       | 10.3 ms             | 0.3 ms |
| 100,000 | 2,097 ms   | 205 MB  | 19.7 ms| 0.01 ms       | 21.3 ms             | 0.6 ms |

The first version took 22 s per query at 50k. `normalized_path_key` resolves through the
filesystem (a stat per path, a network round trip on a share), so query-time code now uses a
pure-string key (`search_key`), numpy row masks for exclusion and filtering, and rankings that
hold row numbers and resolve paths only for the rows asked for. Loading (SQLite read plus
building the key map) grows with the library and must stay off the GUI thread.

BLAS threads: `limited_blas_threads()` caps NumPy to one thread when `threadpoolctl` is
installed (it isn't in this environment, so the numbers above are uncapped). The worker caps
OMP/OpenBLAS/MKL to 2 threads through its environment and runs at below-normal priority with
no console window.

## Tested

- `tests/test_library_semantic.py` (16):
  - reconciling from real per-folder stores (built by the app's own `SemanticFolderIndexTask`
    with a fake encoder);
  - skipping unchanged folders;
  - edited and deleted files dropping out;
  - excluding other model identities;
  - offline folders kept, unsearched, and back when the drive returns;
  - forgetting removed folders;
  - coverage text;
  - search ranking and caching;
  - `similar_to` with negatives, `within`, missing examples and threshold re-cuts;
  - the idle gate;
  - the worker as a real subprocess (fills the folder store; reports offline folders; pauses
    and stops on the control file).
- `tests/test_library_index_controller.py` (3): end to end with a real catalog and the real
  worker process. Start takes the library from 0% to 100% searchable and `similar_to` works.
  A worker started while you're active stays paused and resumes on Start. Without the AI
  runtime it says so.
- Existing semantic, snapshot, window, action-sweep and catalog tests pass (233 in the
  targeted run). The action, menu and palette snapshots were regenerated for the three new
  actions and two palette commands.

**Not run against the real TinyCLIP model**: no model is installed in this environment. The
worker's real path is the same `SemanticEmbeddingExtractor` call the per-folder indexer
makes, but it hasn't been exercised here. Nor has a real multi-drive library, a network share,
or the frozen build's `ai_python_runner.exe`.

One unexplained crash at interpreter exit was seen once during a pytest run on this branch. It
didn't reproduce in four stress runs with `-X faulthandler`.

## Notes and open points

- Indexing writes each catalog folder's `.image_triage_ai` directory, exactly as opening the
  folder already does. A read-only drive fails that folder and moves on.
- Coverage counts indexable photos (JPEG, PNG, TIFF, WebP and RAW, by extension) from the
  catalog. It is only as current as the last catalog refresh.
- A catalog refresh or a model change doesn't trigger a new pass by itself. The next idle tick
  after a restart, or Start, does. Worth hooking `refresh_catalog_index` to `start` later.
- `vector_index()` loads synchronously; the Discover UI should load it in a background task.

## Left for Find Similar / Discover

- UI: a review grid of a `RankedResult` with a similarity slider (`count_at_least` for the
  live count, `cut` for the rows), "More like this" / "Less like this" (positives/negatives),
  and Add Selected / Add All to a collection.
- Text queries via `text_query_vector` with the CLIP text encoder the per-folder search uses.
- Scope and filters (winners, date range, camera/lens, people) by passing `within=` from the
  existing filter code.
- Per-collection "Discover": the same grid seeded from a collection's members (clustering
  members into themes is the follow-up).
