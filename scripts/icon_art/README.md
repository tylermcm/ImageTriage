# App icon source

The app icon is the mark in the top left of the QML shell (`image_triage/qml_shell/qml/TopBar.qml`): a rounded square filled with a conical
gradient from the accent colour through a lavender and back. `render.py` draws it with the same numbers (corner radius a third of the side,
the gradient centred and starting at 210 degrees, stops at 0, 0.5 and 1) in the default dark theme's accent, and writes every file that
carries it:

    python scripts/icon_art/render.py          # image_triage/ui/assets/app_icon-v4.{ico,png}, build_assets/icons/image_triage-v4.{ico,png},
                                               # website/assets/image-triage-icon.png and scripts/qml_parity/help_icon.png
    python scripts/icon_art/render.py OUTDIR   # only the frames (icon_<px>.png), to look at

Each size of the .ico keeps its own render (16 to 256 px) rather than being scaled down from a big one. Bump `VERSION` in `render.py` when the
icon changes (the file names carry it, and `freeze_support.py`, `image_triage/main.py`, `ui/file_associations_dialog.py` and `HelpWindow.qml`
name the files), and delete the old ones. The previous icon, an amber aperture on a dark tile (v3), is in the git history.
