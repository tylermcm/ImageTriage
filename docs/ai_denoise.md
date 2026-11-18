# AI Denoise

Detail > **Denoise** in the RAW editor, beside Super Resolution, as in Camera Raw: a checkbox and an **Amount** slider (0-100, starts
at 50). AI noise reduction on the raw sensor data, before PhotoCraft demosaics and develops the photo.

## Model and licence

- **RawNIND UtNet2, Bayer variant**: B. Brummer and C. De Vleeschouwer, "Learning Joint Denoising, Demosaicing, and Compression from
  the Raw Natural Image Noise Dataset" (2025), https://arxiv.org/abs/2501.08924, code https://github.com/trougnouf/rawnind_jddc.
- The authors' README (commit e80fb7b, 2026-07-04, by Benoit Brummer): "The weights are dual licensed GPL3 + CC-BY-4.0". We use them
  under **CC-BY-4.0**: credit the authors and paper, link the licence, and say we changed them (converted to ONNX). The GPL-3
  training code is not shipped. Training data: RawNIND (CC BY-SA 4.0); see `ai_model_licence_audit.md` (amber).
- The checkpoint is in the authors' Google Drive folder under `rawnind_denoise/<name below>/saved_models/iter_4350000.pt`.
  The export script loads only `rawnind/models/raw_denoiser.py` from their code, so their training dependencies are not needed.
- Checkpoint `DenoiserTrainingBayerToProfiledRGB_4ch_2024-02-21-bayer_ms-ssim_mgout_notrans_valeither_-4`, iteration 4350000 (the one
  darktable 5.6 ships), converted by `scripts/export_rawnind_onnx.py` (`rawnind_bayer.onnx`, 30 MB, sha256 in `ai_model.py`).
- Installed from the AI Center (AI > AI Center, **AI Denoise (RAW editor)** > Install from file…): the picked `.onnx` is recognised by
  its SHA-256, copied into the managed `Editor/RawNIND` model folder and checked again (`editor_models.py`; Super Resolution's two
  Real-ESRGAN files the same way).

## Choosing it (spike, 2026-11-17)

Held-out RawNIND scenes (Canon 7D, Sony A7C, ISO 6400-12800), 1024 px crops, PSNR against the clean low-ISO shot:

| | Noisy | OpenCV NL-means | NAFNet-SIDD w32 | RawNIND linear | RawNIND Bayer |
|---|---|---|---|---|---|
| 7D ISO 12800 | 19.3 | 19.2 | 31.5 | 32.3 | 32.3 |
| A7C ISO 12800 | 23.0 | 24.0 | 32.8 | 35.6 | 35.3 |
| s / megapixel, 4 CPU cores | | 0.5 | 11.6 | 2.3 | **0.4** |

NAFNet (MIT, smartphone training data) smears texture; RawNIND keeps it. A whole 24 MP Sony file takes about 10 s on 4 CPU cores.

## How it works

The denoised photo is **the original raw file with the denoised sensor samples appended** (a trailer). Every TIFF-style raw reader
ignores bytes past the structures it points at, so the file still identifies, previews and reads its metadata as the original; only
PhotoCraft's raw decoder (`photocraft_raw::denoised`, fork branch `codex/ai-denoise`) reads the trailer and replaces the samples of
the denoised region, blended with the originals by the amount written in the trailer. White balance, colour, look and every slider
are the original's.

1. The preview worker writes the crop's CFA samples, normalised 0 (black) .. 1 (white), with the CFA pattern and daylight white
   balance (`denoiseInput`; the worker answers `"denoise": 1` in its hello).
2. The model runs on the warm AI host (`mask_engine_service.infer_denoise`, `denoise_worker.py`): `raw_denoise` packs the samples as
   R, G1, G2, B planes (white-balanced), runs 256 px tiles with a 32 px feathered overlap, matches the output's gain to the input,
   undoes the white balance and keeps one channel per photosite. The network's output has a small bias that repeats every 2 x 2
   pixels, so read back at the photosites the two greens came apart (a maze after demosaic); each CFA site therefore gets back its own
   local level (the heavily blurred difference to the original is added to it). Photosites within 3% of white keep their own value.
3. `ai_denoise.make` appends the result to a copy of the raw in the photo's edits folder
   (`<edits>/denoised/<name>.denoised.<ext>`) and records what it was made from (photo signature, model version).
4. When Denoise is on and that file is current, the preview worker, the hosted editor's renders and full-size exports develop it in the
   photo's place (`ai_denoise.render_source`). Amount rewrites four bytes of the file. An export with Denoise on makes the file if the
   editor never did, as Super Resolution is applied at output.

The setting is `aiDenoise: {"enabled", "amount"}` in the recipe, a host setting (never sent to PhotoCraft), photo-specific (not in
presets or copy/paste), and counts as an edit.

## Not done

- Non-Bayer sensors (Fujifilm X-Trans, Foveon, LinearRaw DNGs) and sensors with a linearization table are refused. The linear
  RawNIND variant could cover X-Trans after demosaic.
- No download yet: the converted `.onnx` is not hosted anywhere at a pinned revision, so it is installed from a file.
- Before shipping commercially: ask Benoit Brummer for written confirmation that the CC-BY-4.0 option covers these checkpoints, and
  add the attribution to the About box and the licences file.
