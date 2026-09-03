# TinyCLIP Retrieval Quality

COCO128: 128 images, 64 object queries, 10 multi-object queries.

| Query set | Current mAP | TinyCLIP mAP | Relative change | Current P@5 | TinyCLIP P@5 |
|---|---:|---:|---:|---:|---:|
| All | 0.563 | 0.538 | -4.5% | 0.432 | 0.411 |
| Objects | 0.607 | 0.584 | -3.7% | 0.463 | 0.450 |
| Multi-object | 0.286 | 0.242 | -15.4% | 0.240 | 0.160 |

TinyCLIP wins 34 queries, loses 32, and is effectively tied on 8.

## Caption-to-image retrieval

| Model | Captions | R@1 | R@5 | R@10 | MRR | Median rank |
|---|---:|---:|---:|---:|---:|---:|
| Current CLIP | 640 | 0.816 | 0.970 | 0.992 | 0.884 | 1.0 |
| TinyCLIP | 640 | 0.758 | 0.983 | 0.994 | 0.851 | 1.0 |

## Largest TinyCLIP regressions

| Query | Kind | Current AP | TinyCLIP AP | Delta |
|---|---|---:|---:|---:|
| cell phone | object | 0.820 | 0.290 | -0.530 |
| kite | object | 1.000 | 0.532 | -0.468 |
| tie | object | 0.657 | 0.252 | -0.404 |
| toilet | object | 1.000 | 0.611 | -0.389 |
| mouse | object | 0.519 | 0.132 | -0.386 |
| bed | object | 1.000 | 0.639 | -0.361 |
| cake | object | 1.000 | 0.646 | -0.354 |
| wine glass | object | 0.853 | 0.499 | -0.353 |
| microwave | object | 0.625 | 0.319 | -0.306 |
| couch | object | 0.967 | 0.710 | -0.257 |

## Largest TinyCLIP gains

| Query | Kind | Current AP | TinyCLIP AP | Delta |
|---|---|---:|---:|---:|
| baseball bat | object | 0.500 | 1.000 | +0.500 |
| baseball glove | object | 0.416 | 0.875 | +0.459 |
| bird | object | 0.500 | 0.833 | +0.333 |
| donut | object | 0.700 | 1.000 | +0.300 |
| stop sign | object | 0.750 | 1.000 | +0.250 |
| tennis racket | object | 0.786 | 1.000 | +0.214 |
| fork | object | 0.195 | 0.391 | +0.196 |
| sandwich | object | 0.556 | 0.750 | +0.194 |
| laptop | object | 0.525 | 0.700 | +0.175 |
| sports ball | object | 0.366 | 0.537 | +0.171 |

Object-label metrics measure presence only. Caption retrieval additionally tests actions, relationships, colors, and scenes against the exact source image.
