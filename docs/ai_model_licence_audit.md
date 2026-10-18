# AI model and licence audit

Checked 17 October 2026. This is a technical review of what each model's published licence says, not legal advice.
Before shipping commercially, have a lawyer confirm the red and amber items.

## How this was checked

- The model list comes from `MODEL_BUNDLES` in `image_triage/ai_manifest.py`: eight bundles, each pinned to a commit.
- For each repo I read the Hugging Face API metadata and, where it existed, the README and LICENSE file at the pinned commit.
  I also read the upstream project's own licence page.
- The Hugging Face metadata was read at the repo's current head, not the pinned commit. Licences rarely change, but the pinned
  commit's own card was read only for AuraFace (README, LICENSE.md), TOPIQ, CLIP, Depth Anything, BiRefNet and OneFormer.
- Page content was read through a summarising fetch tool, so quoted wording is short and should be re-read at source before it
  goes into a legal file.
- Anything I could not open is listed under "Could not verify" below. I did not fill gaps from memory unless marked so.
- Not covered: Python packages (torch, transformers, onnxruntime, insightface, opencv, scikit-learn), the PhotoCraft editor's own
  models, and any model a user supplies through the `*_REPO_ID` environment overrides.
- Not a third-party model: the per-folder winner learner (`quality/winner.py`), the adapter and the classical technical scores
  are trained or computed locally from the user's own ratings, so they carry no third-party licence.

## Summary

| Bundle | Used for | Source | Licence found | Commercial use | Verdict |
|---|---|---|---|---|---|
| TOPIQ (`topiq_nr.onnx`) | Base quality score in AI culling | `Skulleton12/TOPIQ` (a personal ONNX conversion) | Repo says `unknown`; upstream IQA-PyTorch is PolyForm Noncommercial 1.0.0 | **No** (upstream); unknown for the conversion | **Red** |
| AuraFace bundle, files `scrfd_10g_bnkps`, `2d106det`, `genderage` | Face detection, eye points, age and gender | `fal/AuraFace-v1` | Repo is Apache-2.0, but these three files look like InsightFace model-zoo files, which are non-commercial | **Probably no** for these three | **Red** (provenance unverified) |
| AuraFace `glintr100` | Face recognition embeddings (people tagging) | `fal/AuraFace-v1` | Apache-2.0; card says trained on "commercially and publicly available" data | Yes, per the card | Green |
| TinyCLIP ViT-8M/16 | Semantic index and text scoring | `onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX` from `wkcn/TinyCLIP-...` | MIT | Yes (licence); training-data terms unclear | Amber |
| OpenAI CLIP ViT-B/32 | Optional "semantic sidecar" | `openai/clip-vit-base-patch32` | Code MIT; model card has no licence field | Licence yes; **card says deployed use is out of scope** | Amber |
| OneFormer ADE20K Swin-T | Scene segmentation for masks | `shi-labs/oneformer_ade20k_swin_tiny` | MIT | Licence yes; training images are non-commercial research | Amber |
| BiRefNet | Subject matting for masks | `ZhengPeng7/BiRefNet` | MIT | Licence yes; training-data terms unclear | Amber |
| SAM 2.1 Hiera-Tiny | Click-to-select masks | `facebook/sam2.1-hiera-tiny` | Apache-2.0 | Yes | Green |
| Depth Anything V2 Small | Depth effects | `depth-anything/Depth-Anything-V2-Small-hf` | Apache-2.0 (Small only) | Yes | Green |

## Findings

### Red: TOPIQ quality scoring

- The weights are an ONNX file from a personal Hugging Face account. Its metadata says `license: unknown`, there is no LICENSE
  file, and the README has only front matter (no origin or training data stated).
- TOPIQ is the architecture from the IQA-PyTorch (pyiqa) project. That repo's LICENSE file is **PolyForm Noncommercial 1.0.0**
  ("Any noncommercial purpose is a permitted purpose"), and its README adds the NTU S-Lab licence for some components. Its README
  also says datasets are collected for academic, research and educational purposes and tells users to follow each dataset's terms.
- I could not confirm which pyiqa checkpoint the conversion came from or what it was trained on. If it is a KonIQ-10k checkpoint,
  the dataset's own terms apply as well. I did not verify KonIQ-10k's licence.
- TOPIQ feeds the base score that the per-folder ranking blends with, so removing it changes results and needs the plan below.

### Red: three AuraFace files that appear to be InsightFace's

- `fal/AuraFace-v1` is Apache-2.0 and its LICENSE.md is the Apache 2.0 text. Its card says AuraFace "was trained on a commercial
  dataset" and that it enables "usage in commercial setting". The card describes only the recognition model.
- The repo also holds `scrfd_10g_bnkps.onnx`, `2d106det.onnx` and `genderage.onnx`. The card never mentions them.
  InsightFace's model zoo states "ALL models are available for non-commercial research purposes only", and its antelopev2 and
  buffalo_l packs contain an SCRFD-10GF detector, 2d106 alignment and Gender&Age models. The file names match; I did not
  compare file hashes against the InsightFace packs, so provenance is **suggestive, not proven**.
- Until fal confirms these three files are their own, treat them as non-commercial. The `1k3d68.onnx` file in the repo is not
  downloaded by the app.
- Separately, age and gender estimation carries privacy and regulatory risk in some markets whatever the licence says. The
  inspector labels them as estimates; consider removing them (see below).

### Amber items

- **OpenAI CLIP ViT-B/32.** OpenAI's CLIP repo is MIT (confirmed). The Hugging Face card states a different intent:
  "Any deployed use case of the model - whether commercial or not - is currently out of scope." That is a research-use
  statement, not a licence term, but it is the sort of text a customer's legal team will find. The bundle is optional
  (setup defaults the semantic model download to off), so the simplest fix is to drop it.
- **TinyCLIP.** MIT on the Hugging Face card, and the card points to a LICENSE in `microsoft/Cream`, whose text I could not open.
  The models were pretrained on YFCC-15M. I could not confirm the distillation teacher (the card does not say), so I cannot
  say whether any OpenAI CLIP terms carry over. The ONNX copy is a community conversion that has no LICENSE file of its own.
- **OneFormer ADE20K.** Weights are MIT-labelled. ADE20K images come with MIT CSAIL terms that restrict the images to
  non-commercial research and education, while the annotations are reported as BSD-3-Clause. Whether training a model on the
  images carries those limits is a legal question the weights' MIT label does not settle. I did not read CSAIL's current terms page.
- **BiRefNet.** MIT-labelled, trained on DIS-TR (DIS5K). The DIS project's README (2022) says DIS5K is not restricted from
  commercial use at present but commercial users must sign a separate agreement with the authors. I did not see the current
  terms PDF, and the card does not list the datasets for its other tasks.

### Green items

- **SAM 2.1 Hiera-Tiny:** Meta states the checkpoints and code are Apache-2.0. The SA-V dataset terms were not retrieved.
- **Depth Anything V2 Small:** Apache-2.0. The project states that **Base and Large are CC-BY-NC-4.0**, so never swap a larger
  size into the `depth` bundle without a new review.
- **AuraFace glintr100:** Apache-2.0, but the training data is not named, so "commercially and publicly available" is the
  maker's claim and cannot be independently checked.

## Obligations that apply even to the green and amber items

- Apache-2.0 (SAM 2.1, Depth Anything Small, AuraFace): keep the licence text, state changes, and carry any NOTICE file.
- MIT (CLIP, TinyCLIP, OneFormer, BiRefNet): keep the copyright and permission notice.
- Redistribution: the app downloads models from Hugging Face at setup rather than bundling them, which avoids redistributing the
  weights in the installer. If models are ever bundled, the notices above must ship with them.
- Ship a third-party notices page listing each model, its source and licence.

## UniFace: a newer face library with a per-weight licence table

UniFace (`yakhyo/uniface`, MIT code, PyPI first release 1.0.0 on 9 November 2025, 4.1.0 on 17 October 2026, Python 3.10 to 3.14,
ONNX Runtime) wraps many face models: detection, recognition, landmarks, face quality, attributes, parsing, matting. Its README
says "some pretrained weights are not [MIT], so check licences before shipping commercially", and its weights page
(`huggingface.co/yakhyo/uniface-weights`) lists each upstream and licence. Read 17 October 2026:

| Role | Weight family | Upstream licence per that page |
|---|---|---|
| Detection | RetinaFace (mnet, r18 to r50), CenterFace | MIT |
| Detection / landmarks | BlazeFace, Face Mesh (MediaPipe) | Apache-2.0 |
| Landmarks | PIPNet | MIT |
| Face quality | eDifFIQA (T, S, M, L) | MIT |
| Recognition | AdaFace (MIT), EdgeFace (BSD-3-Clause) | permissive |
| Recognition | ArcFace | **non-commercial research only** (InsightFace) |
| Detection | SCRFD | **non-commercial research only** (InsightFace) |
| Landmarks | 2d_106 | **non-commercial research only** (InsightFace) |
| Attributes | Age/Gender | **non-commercial research only** (InsightFace) |
| Detection / segmentation | YOLOv5-Face, XSeg | GPL-3.0 |
| Detection, emotion, FairFace | YOLOv8-Face, DDAMFN, FairFace | unresolved, no licence file |
| Recognition | SphereFace, MobileFace | MIT repo, but trained on MS1M-V2, flagged non-commercial |

Two things follow:

- **It supports the AuraFace finding.** Our bundle's `scrfd_10g_bnkps`, `2d106det` and `genderage` match the names of
  InsightFace weights that UniFace's own table marks non-commercial research only. That is still circumstantial (no hash
  comparison), but it moves the three files from "suspicious" to "treat as non-commercial".
- **Permissive options exist for every role we need.** Detection (RetinaFace or CenterFace), landmarks for eye points and
  blink (MediaPipe Face Mesh or PIPNet), face quality (eDifFIQA) and recognition (EdgeFace, or keep AuraFace `glintr100`).
  Caveat: the page records upstream code and weight licences, not the licences of the training datasets (for example WIDER
  FACE for detectors), and says it is provenance, not legal advice. The same training-data risk discussion as above applies.

## Permissive alternatives

These are options to evaluate, not drop-in answers. Licences marked "confirmed" were read today; the rest need checking.

| Need | Option | Licence status |
|---|---|---|
| Face detection | YuNet (OpenCV Zoo), or RetinaFace / CenterFace via UniFace | MIT, confirmed (YuNet files; UniFace table for the others) |
| Face recognition | SFace (OpenCV Zoo) | Apache-2.0, confirmed for the files; training data not stated |
| Face recognition | Keep AuraFace `glintr100` | Apache-2.0 (above) |
| Eye points and blink | MediaPipe Face Mesh or PIPNet via UniFace | Apache-2.0 / MIT per the UniFace table |
| Face quality | eDifFIQA via UniFace | MIT per the UniFace table |
| Age and gender | Remove the feature | n/a |
| Embeddings | A LAION OpenCLIP model, e.g. `laion/CLIP-ViT-B-32-laion2B-s34B-b79K` | MIT metadata, confirmed; no LICENSE file and no ONNX export in that repo |
| Image quality | Replace TOPIQ with locally computed technical scores plus a quality head trained on the user's own ratings over permissive embeddings | No third-party licence; needs the evaluation below |
| Image quality | Ask the pyiqa authors or the conversion's owner for a commercial licence | Unknown |
| Subject matting and masks | Keep SAM 2.1 for masks; for matting either accept BiRefNet after review or evaluate another MIT/Apache model | Not checked |

## Could not verify

- Where the `Skulleton12/TOPIQ` weights came from and what they were trained on.
- Whether `scrfd_10g_bnkps`, `2d106det` and `genderage` in `fal/AuraFace-v1` are byte-identical to InsightFace's files.
- The text of the TinyCLIP LICENSE in `microsoft/Cream`, and TinyCLIP's distillation teacher.
- MIT CSAIL's current ADE20K terms, the current DIS5K terms, the SA-V dataset terms, and KonIQ-10k's licence.
- Licences of the Python packages the AI runtime installs.

## Suggested actions, in order

1. Decide what replaces TOPIQ, since the base score depends on it (this ties into the quality plan, part 3b).
2. Ask fal whether the three non-glintr100 files are their own work. Until answered, use YuNet for detection and drop
   `2d106det` and `genderage`, or restrict those features to non-commercial builds.
3. Drop the optional OpenAI CLIP sidecar, or keep it with a note that it is research-intent.
4. Get a lawyer's view on OneFormer/ADE20K and BiRefNet/DIS5K training-data terms, or replace them.
5. Add a third-party notices page and a release check that fails if a bundle's licence field is not in an allowed list.
