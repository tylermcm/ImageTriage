# Memory profiling (memtrace)

Measurement only. Nothing here changes how Image Triage caches, decodes or frees memory; it records what is happening so a
baseline exists before any architecture decision (including a Rust or egui prototype) is judged against it.

## What it records

| Layer | Source | How exact |
|---|---|---|
| Private bytes, working set, OS peak working set of the app | Windows `GetProcessMemoryInfo` (`PrivateUsage`, `WorkingSetSize`, `PeakWorkingSetSize`); Linux `/proc` | **Operating system figures** |
| The same for every child process, split into `photocraft`, `ai` (Python workers) and `other` | Process snapshot, descendants of the app | Operating system figures |
| Thumbnail QImage cache, grid pixmap cache, popout preview cache, focus-assist, inspection stats, metadata caches, popout current images, semantic matrix and its query cache | Counters in each cache: bytes, peak, entries, hits, misses, evictions, invalidations | `exact` (a byte count the cache keeps or `QImage.sizeInBytes()`/`ndarray.nbytes`) or `estimated` (width x height x 4, sampled `sys.getsizeof`) — named in every row |
| Decode jobs: queued, running, estimated bytes in flight, largest finished image | Wrapped around `PreviewTask` and `ThumbnailTask` | In-flight bytes are **estimates** (requested size x 4); largest result is exact |
| Pillow-fallback and libraw decodes | Counters in `imaging.py` | Pillow: estimate (3 full-size buffers); libraw: exact array size |
| Short transients (semantic matrix load, ONNX session build, a whole indexing run) | `memtrace.measure`, private bytes sampled every 10 ms | Operating system figures, sampled |
| Longest GUI-thread stall per sample | 50 ms heartbeat timer measuring lateness | Measured |
| Navigation latency next to memory | `nav.finished` events from the existing `NavigationTimer` | Measured |
| Python vs native split (optional) | `IMAGE_TRIAGE_MEMTRACE_TRACEMALLOC=1` | Python-domain allocations only; slows the app |
| GPU memory (optional) | `IMAGE_TRIAGE_MEMTRACE_GPU=1` -> `nvidia-smi` every 5 s | Total GPU memory; per process is usually unavailable on Windows (WDDM) |

Process figures and cache figures are never added together. The report shows the gap between them.

Everything is written into the normal performance log (`performance_*.jsonl`) as `mem.*` events, on the same clock as
`preview.task`, `thumbnail.task` and the others.

## Zero impact when off

`IMAGE_TRIAGE_MEMTRACE` unset (or `0`): no thread, timer or file is created; no memory is sampled; each cache keeps a `None` and pays
one `is None` test per operation; each decode task pays one extra function call. `tests/test_memtrace.py` asserts that nothing is
registered and no thread starts, and that a cache behaves identically with and without it.

While on, the performance logger is forced on and its focus filter lifted (the log is large: several MB per minute of
browsing). The Settings toggle cannot turn it off for that run.

## Profiling session on Windows (PowerShell)

Use the checkout that contains this branch (`ccr-7e0ce324-weramf`), running from source so the instrumented modules are the ones
that run.

```powershell
cd C:\path\to\ImageTriage
git fetch origin ccr-7e0ce324-weramf
git switch ccr-7e0ce324-weramf           # or: git checkout ccr-7e0ce324-weramf

# once: the same environment you normally run Image Triage from
python -m pip install -e .

# a folder for the logs, so they are easy to find and clean up
$env:IMAGE_TRIAGE_LOG_DIR = "$HOME\memtrace_logs"
$env:IMAGE_TRIAGE_MEMTRACE = "1"
$env:IMAGE_TRIAGE_MEMTRACE_INTERVAL_S = "1"
# optional extras (each makes the run a little heavier):
# $env:IMAGE_TRIAGE_MEMTRACE_TRACEMALLOC = "1"   # Python vs native split
# $env:IMAGE_TRIAGE_MEMTRACE_GPU = "1"           # nvidia-smi totals
```

### A. Scripted, repeatable workload (recommended first)

Use two real folders from a shoot (one with mixed JPEG + NEF if you have it; a few hundred to a few thousand photos each).
Close other apps; do not touch the machine while it runs (about 6-8 minutes with the defaults).

```powershell
python scripts\memtrace_workload.py `
    --folder "D:\Photos\Shoot1" --folder "D:\Photos\Shoot2" `
    --browse-count 300 --folder-cycles 10 --openclose-count 30 --idle-seconds 120
```

It runs the real window through: `settle` (baseline) -> `open` a folder -> `scroll` the grid end to end and back -> `browse`
the popout 300 photos forward then 100 back -> `openclose` the popout 30 times -> `folders` (10 changes between your two folders) ->
`semantic` (waits for semantic indexing if the AI runtime and a CLIP model are installed and the folder is not indexed yet) ->
`idle` (120 s, then one forced `gc.collect()`). PhotoCraft is started in idle time like a normal session; add `--no-editor` to
leave it out. Subsets: `--phases open,scroll,browse`.

At the end it prints the paths and writes the report. Without any `--folder` it generates two synthetic JPEG folders; use that only
to check the setup works, not for conclusions.

### B. A manual session

Start the app the normal way with the same environment variables set:

```powershell
python -m image_triage
```

Do an ordinary culling session for 20-30 minutes. To get phase boundaries into the log without any code, there are none in
manual mode: the report then shows the whole timeline and peaks, but not per-phase tables. Close the window normally so the
final reading is written, then leave it a minute before closing if you want the idle figure.

### Making the report

The scripted run makes it automatically. For any log:

```powershell
python scripts\memtrace_report.py "$HOME\memtrace_logs\performance_<stamp>_<pid>.jsonl" `
    --nav "$HOME\memtrace_logs\nav_timing_<stamp>.jsonl"
```

Writes `memtrace_report_<stamp>_<pid>.md`, `.html` (charts; open in a browser) and `.json` (for comparing runs or a prototype) beside the
log. The standard library is all it needs.

### What to send back

The `.html` and `.json` reports of **three** runs of workload A, plus (if you can) a Task Manager "Details" screenshot of
`python.exe`, `photocraft.exe` and the AI worker rows at the end of the `idle` phase. A single run is an anecdote.

### C. The semantic matrix on its own

```powershell
python scripts\memtrace_matrix_probe.py --vectors 100000
```

Builds a synthetic store, loads it with the real loader in a fresh process and prints the matrix's exact size against the OS's
private bytes before, at the sampled peak and after. Run it at 20000, 50000 and 100000 vectors. It measures the current loader; it does not
change it. (On the Linux container used to develop this: 100,000 vectors = 195 MiB matrix, peak +610 MiB = 3.1x, and 2.1x still held
after the load returned. Windows may differ; that is why it is worth running there.)

## Independent Windows cross-checks (no code needed)

These confirm the numbers the sampler reports:

```powershell
# private bytes and working set of the app, once a second, to a CSV
Get-Process python | Select-Object Id,@{n='PrivateMB';e={[int]($_.PrivateMemorySize64/1MB)}},@{n='WSMB';e={[int]($_.WorkingSet64/1MB)}} 

# performance counters (replace the instance name with the one shown by: typeperf -q Process | findstr /i python)
typeperf "\Process(python)\Private Bytes" "\Process(python)\Working Set" "\Process(python)\Working Set - Private" -si 1 -o "$HOME\memtrace_logs\typeperf.csv" -y

# GPU (per process memory in Task Manager > Details > add "Dedicated GPU memory"); counters:
typeperf "\GPU Process Memory(*)\Dedicated Usage" -si 1 -sc 60
```

For a heap-level answer to "who holds it" (which this tool deliberately does not guess): Sysinternals **VMMap** on the app process at
the end of the `idle` phase (look at *Heap*, *Managed*, *Private Data*, *Image*), or WPA/UMDH for allocation call stacks.

## Reading the report

* **Retained after returning to idle** compares the settled figure after the forced collection with the baseline taken before the
  workload. Memory that falls only after `gc.collect()` was held by Python reference cycles. Memory that never falls is
  intentional cache, or freed memory the allocator has not returned to the OS; this tool cannot separate those two.
* **Repeat-stability** fits private bytes against folder-change and open/close repeats. "GROWING" means a steady rise
  above 5 MiB per cycle; compare its slope with the exact-cache slope next to it to see whether the growth is a cache filling.
* **Accounted vs. unaccounted** is app private bytes against the exact, non-overlapping caches. The remainder is Python/Qt
  objects, loaded libraries and models, allocator slack and anything not instrumented — the figure that bounds what a different
  language could recover.
* **Semantic matrix load** shows the sampled peak over the matrix's exact size. Compare it with the ~4x expected from reading
  `LibrarySemanticStore.load_matrix`; the code is unchanged so this measures the current behaviour.
* Latency tables are correlations. Run-to-run noise is real; compare medians of three runs.

## Limits

* Windows readers (`GetProcessMemoryInfo`, ToolHelp) are verified here only by structure-size tests; the first Windows run is their
  first real exercise. If `mem.sample` events show `private: 0`, send the log.
* Estimated rows are labelled; never read them as measurements.
* The sampler and the 10 ms transient watcher add small overhead while profiling; do not use latency numbers from a profiling run as the
  application's normal performance (run without `IMAGE_TRIAGE_MEMTRACE` and use `IMAGE_TRIAGE_NAV_TIMING` for that).
