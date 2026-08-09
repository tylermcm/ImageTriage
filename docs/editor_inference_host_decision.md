# Decision Record: Unified Editor Inference Host ("MaskEngine")

Status: **accepted, in progress** (2026-08-08). Fallback: the per-model workers
remain the shipping default until the host is proven; the pre-host commit is
pushed so we can always revert.

## Problem
Each editor AI model runs in its own persistent subprocess. Every torch worker
pays a fixed one-time cost — **~1.4 s torch import + ~6–7 s CUDA context init +
~0.6 s model load** — and holds **its own CUDA context** (hundreds of MB–1 GB of
VRAM overhead independent of weight size). Two models today (BiRefNet subject,
OneFormer scene); more planned (SAM-family promptable, depth, inpaint). N models →
N× CUDA init latency, N× CUDA-context VRAM, and N near-identical service classes.

## Decision
Build a **custom single-process host** (`MaskEngine`) that imports torch **once**,
holds **one shared CUDA context**, and hosts a registry of model engines behind a
common interface. Route all editor mask inference through one service and one
JSON-lines protocol. Do **not** adopt Triton/TorchServe/Ray/LitServe — they are
server-oriented, heavyweight, and hostile to an offline frozen desktop app.

## What makes this cheap to ship (our specifics)
We do **not** freeze PyTorch. The cx_Freeze MSI / AppImage ships only the thin Qt
app + worker **source scripts** (`freeze_support.py` copies them to `ai_workers/*.py`;
`BUNDLE_AI_RUNTIME_SITE_PACKAGES` defaults off). torch/transformers/onnxruntime
live in a **downloaded managed runtime** (`…/image_triage_ai_cache/runtime/…/profiles/{cpu,gpu}`),
resolved via `resolve_ai_runtime_site_packages(device)` and launched with the
managed Python. Therefore:
- The host is **just another source script in `ai_workers/`** — shipping it is a
  one-line `include_files` entry + a packaging-test assertion.
- The real shipping surface is **not cx_Freeze** but the **managed-runtime package
  manifest** (`AI_SITE_PACKAGES_ENTRIES`) and its versioning: adding a model means
  ensuring its deps are in the runtime profile, and bumping the runtime version so
  users with an older download get the new deps.

## Architecture
```
GUI (Qt, no torch)  --JSON-lines over stdio-->  MaskEngine host process
                                                 - imports torch ONCE
                                                 - ONE CUDA context (GPU)
                                                 - engine registry:
                                                     "subject"  -> BiRefNet engine
                                                     "semantic" -> OneFormer engine
                                                     (future: "prompt" -> SAM, ...)
                                                 - load-on-demand, keep resident
                                                 - one lock, one warm/idle lifecycle
```
- **Engine interface** (already satisfied by the existing worker engine classes):
  `warm_imports() -> device`, `load_model(model_dir) -> device`, and an `infer`
  entry (the existing `generate_subject_mask` / `generate_semantic_masks`).
- **Reuse, don't rewrite:** the host imports the existing `birefnet_worker` /
  `oneformer_worker` modules (they are torch-only, Qt-free, torch imported lazily)
  and instantiates their engines in one process — so they share torch + one CUDA
  context automatically.
- **Protocol:** `warm-imports | load-model | infer | shutdown`, each carrying an
  `engine` field for routing; replies tagged `PROGRESS | DEVICE | AI_METRIC |
  RESPONSE` (unchanged, reused).
- **Service:** one `MaskEngineService` generalizes `BiRefNetWorkerService` /
  `OneFormerWorkerService` (lock, request IDs, one-shot respawn-and-retry, idle
  keepalive, warm-on-render). `ensure_subject_masks` / `ensure_semantic_masks`
  route through it.

## Memory / lifecycle policy
- Editor mask models are **small (194–425 MB)** → **load and keep all resident** by
  default; no eviction needed on a real GPU. This matches "keep it loaded."
- LRU / VRAM-budget eviction is a **safety valve** only for CPU-RAM-constrained or
  small-VRAM machines.
- A **future heavy tier** (SAM-H ~2.5 GB, diffusion inpaint ~4 GB) is where eviction
  and/or continued subprocess isolation apply — the "2-tier" split maps to **VRAM
  class**, not just crash risk.
- Warm the **host** once (CUDA init) on first editor use / photo render; ensure a
  specific engine's model is loaded when its tool opens (extends today's
  warm-on-render + idle keepalive).

## Scope boundaries
- **Editor host is torch-only.** SegFormer (our only ORT editor model) is gone, so
  BiRefNet/OneFormer/future SAM are all torch.
- **Leave the culling pipeline separate.** CLIP/TOPIQ/DINO/InsightFace run in the
  ONNX-Runtime `AICullingPipeline` with a batch lifecycle; do not fold it in (torch
  + ORT allocators contend for VRAM; different usage pattern).

## Risks & mitigations
1. **Single point of failure** — one crash/OOM kills all warm engines. Mitigate with
   the existing per-request exception isolation + one-shot respawn-and-retry, and by
   keeping the per-model workers as a **fallback tier** during migration.
2. **CUDA OOM is not reliably recoverable in-process** → supervised host restart;
   the GUI retries the request against a fresh host.
3. **Dependency / monkeypatch conflicts** — smaller than generic advice suggests:
   BiRefNet's einops/kornia shim is already **conditional** (`try import / except
   ImportError: inject`), so shipping real einops/kornia in the runtime profile makes
   it a dormant fallback. Pin one shared version set for all editor models.
4. **Managed-runtime dep drift** across versions — bump runtime version when a new
   model adds deps.

## Migration plan (incremental, reversible)
1. Add the host worker (`mask_engine_worker.py`) reusing the existing engines —
   additive, standalone, tested at the routing/protocol level. **← current step**
2. Add a `MaskEngineService` that routes by engine; wire `ensure_semantic_masks`
   through it first (OneFormer), keeping BiRefNet on its old worker.
3. Route `ensure_subject_masks` (BiRefNet) through the host too → both now share one
   process + CUDA context (the actual win).
4. Add a per-engine **fallback**: on host failure for engine X, fall back to spawning
   its legacy worker; keep the legacy workers shipped until the host is proven.
5. Retire the duplicated `*WorkerService` classes once the host is stable.
6. Fold warm-on-render + idle keepalive into the single host lifecycle.

## Observability
Preserve per-phase metrics (`AI_METRIC` import/model_load/inference/…) and the
always-on `execution.log`; the host consolidates all engines onto one metric/log
channel.
