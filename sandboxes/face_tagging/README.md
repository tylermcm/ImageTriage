# Face-tagging sandbox

Validates an **automatic, face-only people pass** (Option 2) before wiring it
into the app. It answers two questions on real photos:

1. **Performance** — is InsightFace detection + identity embedding fast enough to
   run automatically when a folder opens?
2. **Clustering quality** — do the identity vectors cluster into sensible people?

It intentionally drives the production code paths so the results transfer:

- `PreviewExtractor` for decoding (RAW → embedded JPEG, like the app),
- `FaceQualityAnalyzer(enable_identity=True)` — the real InsightFace wrapper,
- `upsert_faces` + `cluster_face_identities` — the real people-clustering.

Nothing is written to your library; faces live in an in-memory SQLite for the
run only.

## Requirements

- AI runtime installed (AI > Runtime And Cache > Install AI Runtime) so
  `onnxruntime` + `insightface` import.
- The AuraFace pack (Apache-2.0) — auto-downloaded to `--auraface-root` on first run.

## Run

```
.msi_build_venv\Scripts\python.exe -m sandboxes.face_tagging.cli \
    --folder "C:\path\to\photos" --limit 300 --html people.html
```

Useful flags:

- `--limit N` — cap images (start small to check timing).
- `--threshold 0.62` — clustering cosine threshold (higher = stricter/more clusters).
- `--device cuda` — use the GPU if the GPU runtime is installed.
- `--html people.html` — contact sheet grouped by person, written under `results/`,
  open it in a browser to eyeball clustering quality.

The console prints throughput (img/s), per-image detect/decode timing, projected
time for 1k/5k images, and cluster sizes.

## Subject vs background faces

To keep only faces that belong to the subject (drop incidental background
strangers), add:

- `--faces-html faces.html` — writes a KEPT (green) / DROPPED (red) contact sheet
  of face crops under `results/`, each labelled with its signals
  (`area%`, `rel` size vs the largest face, `sharp`, `sal`, `det`). Eyeball it to
  confirm dropped faces are really background.
- `--min-face-frac 0.015` — drop faces smaller than this fraction of the image.
- `--min-rel-area 0.30` — drop faces smaller than this fraction of the largest
  face in the same image.
- `--salience` — also require BiRefNet salience overlap (needs the BiRefNet model
  installed). `--min-salience 0.15` sets the cutoff. This is the semantically
  correct "is this person the subject" signal; try it when size alone can't
  separate a sharp background person from the subject.

Size + relative-size are free (from the face box); salience adds a BiRefNet pass
per image that has a face, so it is opt-in.
