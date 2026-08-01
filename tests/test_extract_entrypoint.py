from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class ExtractEntrypointTests(unittest.TestCase):
    def test_deferred_include_marker_is_exposed_by_cli(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        script = workspace / "AICullingPipeline" / "scripts" / "extract_embeddings.py"

        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("--include-paths-ready-file", completed.stdout)

    def test_worker_style_import_does_not_load_full_ai_engine(self) -> None:
        workspace = Path(__file__).resolve().parents[1]
        script = workspace / "AICullingPipeline" / "scripts" / "extract_embeddings.py"
        probe = (
            "import runpy, sys; "
            f"runpy.run_path({str(script)!r}, run_name='__mp_main__'); "
            "print(int('app.engine' in sys.modules), "
            "int('torch' in sys.modules), int('transformers' in sys.modules))"
        )

        completed = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual("0 0 0", completed.stdout.strip())


if __name__ == "__main__":
    unittest.main()
