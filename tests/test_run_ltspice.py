from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_ltspice as runner  # noqa: E402
from run_ltspice import build_command, stage_asc_with_dependencies  # noqa: E402
from validate_log import find_errors  # noqa: E402


class LTspiceCommandTests(unittest.TestCase):
    def test_source_stepping_recovery_clears_prior_newton_diagnostic(self) -> None:
        log = (
            "Direct Newton iteration failed to find operating point.\n"
            "Gmin stepping failed to find operating point.\n"
            "Source stepping succeeded in finding the operating point.\n"
        )
        self.assertEqual(find_errors(log), [])

    def test_newton_diagnostic_without_later_recovery_remains_fatal(self) -> None:
        self.assertEqual(
            find_errors("Direct Newton iteration failed to find operating point.\n"),
            ["Direct Newton iteration failed to find operating point."],
        )

    def test_binary_raw_is_the_default(self) -> None:
        command = build_command(Path("LTspice.exe"), Path("circuit.net"))
        self.assertNotIn("-ascii", command)
        self.assertNotIn("-sync", command)

    def test_ascii_raw_is_opt_in(self) -> None:
        command = build_command(Path("LTspice.exe"), Path("circuit.net"), ascii_output=True)
        self.assertIn("-ascii", command)

    def test_asc_validation_stages_relative_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model_dir = root / "models"
            model_dir.mkdir()
            (model_dir / "op.lib").write_text(".model DUMMY D\n", encoding="utf-8")
            asc = root / "circuit.asc"
            asc.write_text(
                "Version 4\nTEXT 0 0 Left 2 !.include models/op.lib\n",
                encoding="utf-8",
            )
            staged = stage_asc_with_dependencies(asc, root / "stage")
            self.assertIn("!.include models/op.lib", staged.read_text(encoding="utf-8"))
            self.assertTrue((staged.parent / "models" / "op.lib").is_file())

    def test_asc_smoke_artifacts_can_be_routed_to_support_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            support = root / "circuit_files"
            asc = root / "circuit.asc"
            asc.write_text("Version 4\n", encoding="utf-8")
            executable = root / "LTspice.exe"
            executable.write_bytes(b"stub")

            def fake_run(command, **_kwargs):
                target = Path(command[-1])
                if "-netlist" in command:
                    target.with_suffix(".net").write_text("V1 in 0 1\n.op\n.end\n", encoding="utf-8")
                else:
                    target.with_suffix(".raw").write_bytes(b"raw")
                    target.with_suffix(".log").write_text("Simulation successful\n", encoding="utf-8")
                return runner.subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(runner.subprocess, "run", side_effect=fake_run):
                result = runner.run_simulation(
                    asc,
                    executable,
                    output_dir=support,
                    artifact_stem="circuit-asc",
                )
            self.assertTrue(result["ok"])
            self.assertEqual(Path(result["raw"]).parent.resolve(), support.resolve())
            self.assertEqual(Path(result["log"]).parent.resolve(), support.resolve())
            self.assertFalse((root / "circuit-asc.raw").exists())


if __name__ == "__main__":
    unittest.main()
