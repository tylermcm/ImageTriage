# Tone-Control Calibration

Image Triage's Light controls should be calibrated against measured transfer
curves rather than tuned by restricting sliders until failures are hidden.

The research harness generates a controlled lossless target, current Image
Triage renders, a Photoshop export checklist, and an incremental report.

## Prepare a run

```powershell
.\scripts\tone-calibration.ps1 prepare --open
```

The run is written beneath `.benchmarks/tone_calibration/`, which is ignored by
Git. Follow `PHOTOSHOP_STEPS.md` inside the run directory. The first pass needs
only the twelve endpoint exports; the remaining twelve midpoint exports improve
curve fitting.

## Analyze Photoshop exports

```powershell
.\scripts\tone-calibration.ps1 analyze ".benchmarks/tone_calibration/<run>"
```

`report.md` summarizes clipping, lost levels, tonal reversals, and chroma drift.
`curves.csv` contains the measured 256-point RGB transfer curves for fitting and
comparison.

The benchmark does not modify editor behavior. New Light-control math should be
implemented only after the reference exports have been analyzed.
