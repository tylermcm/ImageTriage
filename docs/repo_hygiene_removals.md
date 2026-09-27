# Repo hygiene: verified removal candidates (WI-2.2)

Everything below was checked on 2026-09-26 by searching `image_triage/`, `scripts/`, `packaging/`, `tests/`, `.github/`, the root build scripts and the docs. **Nothing has been deleted.** File removal is yours to run; each command uses a literal path from the repo root `C:\Users\tylle\OneDrive\Documents\Playground`. Run them from there. Commit afterwards, and run `py -3.13 -m pytest tests/test_freeze_support.py` to confirm packaging is unaffected.

## A. Tracked files, safe to remove (0 references anywhere)

| Path | Size | Evidence |
|---|---|---|
| `image_triage/ui/assets/splash_background-v2.png` | 3.1 MB | not loaded by code, not staged by `freeze_support.py` |
| `image_triage/ui/assets/splash_background-v3.png` | 3.1 MB | same |
| `image_triage/ui/assets/splash_background-v6.png` | 3.2 MB | same |
| `image_triage/ui/assets/splash_background.png` | 3.1 MB | same |
| `minus_sign.png` (repo root) | 57 KB | byte-identical to `image_triage/ui/assets/minus_sign.png`, which is the one the code uses |
| `sidebar_icons.zip`, `sidebar_icons_2.zip` | 22 KB | no references |
| `ssh_err.txt`, `ssh_out.txt` | 0 KB | empty files, no references |
| `package-lock.json` | 89 B | an empty lockfile (`"packages": {}`); there is no `package.json` |

```powershell
git rm "image_triage/ui/assets/splash_background-v2.png" "image_triage/ui/assets/splash_background-v3.png" "image_triage/ui/assets/splash_background-v6.png" "image_triage/ui/assets/splash_background.png"
git rm "minus_sign.png" "sidebar_icons.zip" "sidebar_icons_2.zip" "ssh_err.txt" "ssh_out.txt" "package-lock.json"
```

**Keep on purpose:** `splash_background-v7.png` (the splash uses it) and `splash_background-v4.png` (`freeze_support.py` stages it explicitly). Checked against the built MSI tree: the whole package folder ships, so v7 is in the installer and there is no packaging bug. The v4 include is redundant and can go with a later packaging clean-up (WI-4.6), not now.

## B. Decide later (not safe yet)
| Path | Why it stays for now |
|---|---|
| `heartbutton.png`, `xbutton.png` (root) | used by `scripts/loupe_card_prototype.py`; goes with the prototype tooling decision (D13, WI-2.8) |
| `verified.png` (root, 652 KB) | different from the 51 KB `assets/verified.png` the app uses, so it looks like the original artwork. Keep if you want the source art; otherwise it is unreferenced |

## C. Untracked or ignored clutter (not in git; your call)
| Path | Size | Note |
|---|---|---|
| `ES50_EScan2_67810_AM.exe` | 76 MB | an unrelated installer sitting in the repo root; ignored by git, 0 references |
| `_tmp_*.png` (63 files in the repo root) | about 6 MB | old UI screenshots from earlier sessions; ignored by git |
| `image_triage/engine/` | tiny | contains only stale `__pycache__` `.pyc` files: there is **no source** in this folder, so nothing imports it |
| `sandboxes/face_tagging/models` and `sandboxes/tinyclip_benchmark/{models,datasets,export_deps,wheels}` | about 1.1 GB | downloaded models and datasets for the sandbox experiments. Only remove if you no longer run those experiments |

To review the screenshots first, then remove them:

```powershell
Get-ChildItem -Path "C:\Users\tylle\OneDrive\Documents\Playground" -Filter "_tmp_*.png" -File | Measure-Object
Get-ChildItem -Path "C:\Users\tylle\OneDrive\Documents\Playground" -Filter "_tmp_*.png" -File | Remove-Item
Remove-Item "C:\Users\tylle\OneDrive\Documents\Playground\ES50_EScan2_67810_AM.exe"
Remove-Item -Recurse "C:\Users\tylle\OneDrive\Documents\Playground\image_triage\engine"
```

## D. Left alone deliberately
`pocketdrop.dll` policy (commit versus build in CI) depends on D15 and is not decided here. The `pyproject.toml` package declarations are re-checked after WI-4.6.
