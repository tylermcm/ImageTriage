# TinyCLIP Speed Benchmark

- CPU: unknown
- ONNX Runtime: 1.26.0
- Threads: 4
- Images per run: 24
- Repetitions: 3
- Image throughput shown at batch size 4.

| Model | Files | Load | RSS increase | Images/s | Text query | Embedding |
|---|---:|---:|---:|---:|---:|---:|
| Current CLIP ViT-L/14 | 1634.23 MB | 1.586 s | 1659.43 MB | 3.61 | 27.75 ms | 768 |
| TinyCLIP ViT-8M/16 Text-3M | 93.11 MB | 0.149 s | 106.84 MB | 133.38 | 1.44 ms | 512 |

Full batch-size timings and raw repetitions are in `benchmark.json`.
