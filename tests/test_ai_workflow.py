from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from image_triage.ai_workflow import _run_command_with_live_output


class AIWorkflowStreamingTests(unittest.TestCase):
    def test_run_command_streams_lines_and_flushes_trailing_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "stream_case.py"
            script_path.write_text(
                textwrap.dedent(
                    """
                    import sys

                    sys.stdout.write("first line\\n")
                    sys.stdout.write("second line\\n")
                    sys.stdout.write("final tail")
                    sys.stdout.flush()
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            emitted: list[str] = []
            completed = _run_command_with_live_output(
                [sys.executable, str(script_path)],
                cwd=Path(temp_dir),
                progress_callback=emitted.append,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(emitted, ["first line", "second line", "final tail"])
        self.assertIn("final tail", completed.stdout)

    def test_run_command_merges_partial_line_chunks_before_emitting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "partial_case.py"
            script_path.write_text(
                textwrap.dedent(
                    """
                    import sys

                    sys.stdout.write("par")
                    sys.stdout.flush()
                    sys.stdout.write("tial\\n")
                    sys.stdout.flush()
                    sys.stdout.write("tail")
                    sys.stdout.flush()
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            emitted: list[str] = []
            completed = _run_command_with_live_output(
                [sys.executable, str(script_path)],
                cwd=Path(temp_dir),
                progress_callback=emitted.append,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(emitted, ["partial", "tail"])
        self.assertTrue(completed.stdout.endswith("tail"))


if __name__ == "__main__":
    unittest.main()
