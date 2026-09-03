# TinyCLIP benchmark sandbox

This sandbox compares Image Triage's installed CLIP ViT-L/14 ONNX model with
TinyCLIP ViT-8M/16 Text-3M using the same ONNX Runtime CPU provider, thread
count, generated image tensors, prompts, batching, and repetition count.

It does not modify the application's model configuration or existing image
embeddings. TinyCLIP is downloaded and exported only beneath this directory.

Run from the repository root:

```powershell
.msi_build_venv\Scripts\python.exe sandboxes\tinyclip_benchmark\benchmark.py
```

Results are written to `results/benchmark.json` and `results/README.md`.

Run the labeled COCO128 retrieval-quality comparison after preparing the dataset:

```powershell
.msi_build_venv\Scripts\python.exe sandboxes\tinyclip_benchmark\quality_benchmark.py
```

Quality results are written to `results/quality.json`, `results/quality_queries.csv`,
`results/quality_summary.md`, and `results/quality_report.html`. Downloaded datasets, checkpoints, export packages,
and embedding caches remain ignored sandbox artifacts.
