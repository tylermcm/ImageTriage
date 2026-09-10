# Clean-machine MSI validation matrix

The release gate for the AI features is **not** a green test run in the source
checkout. It is this matrix, executed against an installed MSI, with the source
checkout absent from the machine.

## How to run one row

1. Build the MSI: `python setup_msi.py bdist_msi`.
2. Copy **only** the `.msi` to the target machine.
3. Install it.
4. Launch Image Triage, run **AI → AI Setup And Cache → Set Up AI…**.
5. Restart the application.
6. Run **AI → AI Setup And Cache → Check AI Readiness (Demo Ready)…** and record
   the summary line and any failing rows.
7. Run the harness for a machine-readable record:

```bash
python scripts/ai_clean_machine_check.py --install-dir "C:\Program Files\ImageTriage" --json row.json --markdown row.md
```

   On a machine with no Python at all, steps 4–6 alone are the gate; the harness
   is for capturing evidence when a developer Python happens to be present.

8. Complete the **workflow pass** below. Probes are necessary but not
   sufficient — the gate is real work on real photos.

## Workflow pass (per row)

Open a folder of at least 200 real photos and complete each, recording pass/fail:

| # | Workflow | Where |
| --- | --- | --- |
| 1 | AI culling completes and ranks | AI → AI Workflow Center |
| 2 | Semantic text search returns plausible hits | Search bar |
| 3 | Face detection and grouping completes | Sidebar → Face Groups → Review |
| 4 | Scene selection (OneFormer) produces a sky/skin mask | Editor → Masks |
| 5 | Subject selection (BiRefNet) isolates the subject | Editor → Masks |
| 6 | Click selection (SAM) responds to a click | Editor → Masks |
| 7 | Depth estimation produces a depth map | Editor → depth-aware tool |
| 8 | Repair AI runs and the workflows above still pass | AI → Repair AI |

## Matrix

Fill in `PASS` / `FAIL (stage, category)` / `N/A`. A row is only complete when
both the probe column and the workflow column are filled.

| # | Machine condition | Probes | Workflows | Notes |
| --- | --- | --- | --- | --- |
| 1 | Windows 11 x64, NVIDIA GPU, no Python installed | | | GPU path; expect CUDAExecutionProvider active |
| 2 | Windows 11 x64, CPU only, no Python installed | | | Expect selected_device=cpu everywhere |
| 3 | Windows Store Python installed | | | Guards the original field failure |
| 4 | Non-admin account | | | Install per-user or confirm elevation prompt |
| 5 | Username contains a space | | | Path quoting |
| 6 | Username contains non-ASCII characters | | | Encoding in paths and logs |
| 7 | Long paths disabled | | | Managed root is short by design |
| 8 | Documents redirected to OneDrive | | | Folder-local artefacts |
| 9 | Low disk (< 5 GB free) | | | Expect a disk preflight refusal, not a partial install |
| 10 | Offline (no network) | | | Expect a network-category message with a retry action |
| 11 | Proxy with TLS interception | | | Expect the certificate-category message |
| 12 | Install interrupted, then relaunch + Repair AI | | | No manual cleanup permitted |
| 13 | Defender real-time scanning active during install | | | Locked-directory retries |
| 14 | Repair over a healthy install | | | Must not re-download |
| 15 | Repair over a deliberately corrupted model file | | | Must detect and re-download |
| 16 | Upgrade from a build using the old cache layout | | | Migration keeps existing downloads |
| 17 | Uninstall, then reinstall | | | No unrelated user data removed |
| 18 | Two Image Triage windows installing at once | | | Second must be refused, not corrupt |
| 19 | AMD or Intel GPU machine | | | Must select CPU deliberately, not fail |
| 20 | Windows 11 ARM64 | | | Expect a clear unsupported-architecture message |

## Injecting the conditions

Rows that need a specific failure can be forced without special hardware:

| Row | How to force it |
| --- | --- |
| 9 | Point `IMAGE_TRIAGE_AI_ROOT` at a small VHD or a nearly-full volume |
| 10 | Disable the network adapter before Set Up AI |
| 11 | Install a proxy's root CA and route through it, or use a TLS-inspecting proxy |
| 12 | Kill `ai_runtime_installer.exe` mid-install, then relaunch the app |
| 15 | Truncate a file in `%USERPROFILE%\.image-triage\AI\models\...` and run Repair AI |
| 16 | Restore a pre-upgrade `%LOCALAPPDATA%\image_triage_ai_cache` and launch |
| 18 | Start Set Up AI in two copies of the app at once |

## Recording results

Keep each row's `row.json` and the Demo Ready summary. A release is gated on
every row being `PASS` or a consciously accepted `FAIL` with a written
justification — see the residual-risk list in
`docs/ai_runtime_failure_map.md`.
