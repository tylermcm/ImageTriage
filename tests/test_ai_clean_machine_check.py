from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import ai_clean_machine_check as check


class CleanMachineCheckTests(unittest.TestCase):
    def test_normal_setup_capabilities_do_not_require_optional_dino(self) -> None:
        self.assertNotIn("dino", check.CAPABILITIES)

    def test_provider_only_culling_probe_does_not_require_inference_flag(self) -> None:
        payload = {
            "capability": "culling",
            "ok": True,
            "inference_ran": False,
            "selected_device": "cpu",
            "providers_available": ["CPUExecutionProvider"],
            "probe_level": "full",
        }
        process = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with patch.object(check, "_run", return_value=process):
            result = check.probe_capability(Path("C:/app"), "culling", "C:/ai", "cpu")

        self.assertTrue(result.ok, result.detail)

    def test_runner_probe_uses_a_writable_temporary_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir) / "Program Files" / "ImageTriage"
            install_dir.mkdir(parents=True)
            (install_dir / "ai_python_runner.exe").write_bytes(b"")
            site_packages = Path(temp_dir) / "managed" / "site-packages"
            site_packages.mkdir(parents=True)
            process = subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "numpy": str(site_packages / "numpy" / "__init__.py"),
                        "providers": ["CPUExecutionProvider"],
                    }
                ),
                "",
            )
            command: list[str] = []

            def fake_run(args, **_kwargs):
                command.extend(args)
                self.assertTrue(Path(args[1]).is_file())
                return process

            with patch.object(check.subprocess, "run", side_effect=fake_run):
                result = check.check_runner_executes(
                    install_dir, str(site_packages), "cpu"
                )

        self.assertTrue(result.ok, result.detail)
        self.assertNotEqual(Path(command[1]).parent, install_dir)

    def test_installer_stdlib_check_requires_unittest_in_a_fresh_helper_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir) / "ImageTriage"
            install_dir.mkdir()
            (install_dir / "ai_runtime_installer.exe").write_bytes(b"")
            process = subprocess.CompletedProcess([], 0, "AI runtime imports validated.\n", "")

            def fake_run(args, **_kwargs):
                site_packages = Path(args[args.index("--site-packages") + 1])
                self.assertIn("import unittest", (site_packages / "numpy.py").read_text())
                return process

            with patch.object(check, "_run", side_effect=fake_run):
                result = check.check_installer_stdlib_bootstrap(install_dir)

        self.assertTrue(result.ok, result.detail)


if __name__ == "__main__":
    unittest.main()
