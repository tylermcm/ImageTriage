# Models to host for the AI v5 cascade

The cascade downloads its models on demand through the app's model store, which checks every file against a pinned size and SHA-256. Two of the
three have no upstream address we can pin, so they are meant to be hosted on the owner's Hugging Face account. Hosting is safe from a correctness point
of view: a file that does not match the digest below is refused, so any host works.

## 1. MANIQA checkpoint (image quality ranker)

| | |
|---|---|
| File | `ckpt_koniq10k.pt` |
| Size | 543,335,435 bytes |
| SHA-256 | `a207f8ab57322e6be38ff5c8d019301dc032b454bef21c3c9f9dbf7974eebff6` |
| Download | https://github.com/IIGROUP/MANIQA/releases/download/Koniq10k/ckpt_koniq10k.pt |
| Source | github.com/IIGROUP/MANIQA, release "Koniq10k" (trained on KonIQ-10k) |
| Licence | Code: Apache-2.0 (verified, the repo `LICENSE`). Weights: no separate licence stated. Training data (KonIQ-10k): "freely available to the research community", no licence text found. **Unverified for commercial use.** |
| Repo the app expects | `Skulleton12/maniqa-koniq10k`, file at the repo root (`MANIQA_REPO` in `image_triage/cascade/rankers/bundles.py`) |

## 2. MediaPipe Face Landmarker bundle (blink detector)

| | |
|---|---|
| File | `face_landmarker.task` |
| Size | 3,758,596 bytes |
| SHA-256 | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |
| Download | https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task |
| Source | Google AI Edge, MediaPipe Face Landmarker, float16. The URL says `latest`, so it is not a pinned version; the digest above is the file that was validated (checked 19 October 2026). |
| Licence | The model cards for the two models inside it (Face Mesh V2 and Blendshape V2, linked from the Face Landmarker page) each state "LICENSED UNDER Apache License, Version 2.0" (read from the cards); the MediaPipe code is Apache-2.0 (its `LICENSE`). Training data is Google's own captured and synthetic sets per the cards. |
| Repo the app expects | `Skulleton12/mediapipe-face-landmarker`, file at the repo root (`BLINK_REPO` in `bundles.py`) |

## 3. Q-ReAlign Mini (aesthetic ranker): already hosted, no action needed

`q-future/Q-ReAlign-Mini-0.8B` on Hugging Face, pinned in the app to revision `fe1f45a7574c9e9d908875af9f7e90cb946aa19f` with a SHA-256 for every file
(about 2.1 GB: `model.safetensors` 2,214,590,296 bytes plus tokenizer and config files). Declared licence Apache-2.0; its training data (KonIQ, SPAQ, KADID,
AGIQA-20K, AVA, LSVQ) is **unverified for commercial use**. Mirror it only if you want an independent copy, and then change `QREALIGN_REPO`.

## How to host

```bash
curl -L -o ckpt_koniq10k.pt https://github.com/IIGROUP/MANIQA/releases/download/Koniq10k/ckpt_koniq10k.pt
curl -L -o face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
sha256sum ckpt_koniq10k.pt face_landmarker.task      # must match the table above
huggingface-cli upload Skulleton12/maniqa-koniq10k ckpt_koniq10k.pt ckpt_koniq10k.pt
huggingface-cli upload Skulleton12/mediapipe-face-landmarker face_landmarker.task face_landmarker.task
```

Put a short `README.md` in each repo naming the source URL, the licence above and the original authors (Apache-2.0 asks for the licence text and notices to be
kept with redistributed files; add a copy of the Apache-2.0 text as `LICENSE`).

If you use different repository names, change `MANIQA_REPO` and `BLINK_REPO` in `bundles.py` (two lines). For testing before anything is hosted, the
environment variables `IMAGE_TRIAGE_MANIQA_MODEL_DIR` and `IMAGE_TRIAGE_BLINK_MODEL_DIR` point the app at a local folder that already holds the file, and
`IMAGE_TRIAGE_MANIQA_MODEL_REPO_ID` / `IMAGE_TRIAGE_BLINK_MODEL_REPO_ID` override the repository.

**Licence caution before making the MANIQA repository public.** Republishing a checkpoint trained on a research-only dataset can be a problem even when
the code is Apache-2.0. Until the KonIQ-10k terms are checked from a primary source, consider keeping `Skulleton12/maniqa-koniq10k` private or gated (the
app would then need a token to download it), or leave MANIQA out of any public release. The face landmarker file has no such concern.
