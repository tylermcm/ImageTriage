# App icon source

The app icon is the Image Triage logo (`image_triage/qml_shell/qml/LogoMark.qml`): an ember ring, six round blades turned 60 degrees apart and a white
dot, with no tile behind it. `render.py` draws it with the same numbers (a 1024 box, ring radius 374 outside and 314 inside, the gradient stops,
the blades leaned back 22 degrees), scaled up so the ring fills the frame, and writes every file that carries it:

    python scripts/icon_art/render.py          # image_triage/ui/assets/app_icon-v5.{ico,png}, build_assets/icons/image_triage-v5.{ico,png},
                                               # website/assets/image-triage-icon.png and scripts/qml_parity/help_icon.png
    python scripts/icon_art/render.py OUTDIR   # only the frames (icon_<px>.png), to look at

Each size of the .ico keeps its own render (16 to 256 px) rather than being scaled down from a big one. Bump `VERSION` in `render.py` when the
icon changes (the file names carry it, and `freeze_support.py`, `image_triage/main.py`, `ui/file_associations_dialog.py` and `HelpWindow.qml`
name the files), and delete the old ones. The previous icons, an amber aperture on a dark tile (v3) and a gradient rounded square (v4), are in the git history.
