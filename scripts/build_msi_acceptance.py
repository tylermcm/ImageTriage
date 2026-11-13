"""Package an already frozen app for disposable MSI acceptance, without rebuilding.

The version is MSI metadata only: use a higher version for an upgrade fixture,
never publish that fixture as a release. Build the frozen directory with the
current setup_msi.py first. The normal editor/helper bundle environment variables
still apply. Run this with the same Python/Qt environment used for freezing.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frozen", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--name", choices=("base", "upgrade"), required=True)
    args = parser.parse_args()
    frozen, output = args.frozen.resolve(), args.output.resolve()
    if not (frozen / "ImageTriage.exe").is_file():
        parser.error("frozen directory must contain the already built ImageTriage.exe")
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    import cx_Freeze

    config = runpy.run_path(str(repo / "setup_msi.py"), run_name="msi_acceptance_config")
    build = dict(config["build_exe_options"], build_exe=str(frozen))
    msi = dict(config["bdist_msi_options"], skip_build=True, product_version=args.version,
               bdist_dir=str(output / ("stage-" + args.name)),
               dist_dir=str(output / "input"), output_name=args.name + ".msi")
    sys.argv = ["build_msi_acceptance", "bdist_msi"]
    cx_Freeze.setup(name="ImageTriage", version=args.version, description="Image Triage",
                    options={"build_exe": build, "bdist_msi": msi}, executables=config["executables"])


if __name__ == "__main__":
    main()
