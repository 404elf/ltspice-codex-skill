import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import weave_convert as weave  # noqa: E402
import run_validation_intent as intent  # noqa: E402


class WeaveArtifactLayoutTests(unittest.TestCase):
    def test_default_save_directive_is_absent_from_canonical_net_and_asc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "out"
            source.mkdir()
            output.mkdir()
            net = source / "filter.net"
            net.write_text(
                "V1 in 0 AC 1\n"
                ".save V(in) V(out)\n"
                "R1 in out 1k\n"
                ".ac dec 10 10 10k\n.end\n",
                encoding="utf-8",
            )
            ltspice = root / "LTspice.exe"
            ltspice.write_bytes(b"stub")
            config = root / "config.json"
            config.write_text(
                json.dumps({
                    "python": sys.executable,
                    "ltspice": "LTspice.exe",
                    "output_root": "out",
                }),
                encoding="utf-8",
            )
            paths = intent.resolve_paths(net, config)
            canonical = intent.prepare_canonical_net(paths)
            self.assertNotIn(".save", canonical.read_text(encoding="utf-8").lower())

            weave_dir = root / "weave"
            weave_dir.mkdir()
            (weave_dir / "weave.js").write_text("// test stub\n", encoding="utf-8")
            asc = output / "filter.asc"
            result = output / "filter_files" / "filter.weave-verification.txt"

            def fake_run(command, _cwd):
                if "convert" in command:
                    directives = [
                        line for line in canonical.read_text(encoding="utf-8").splitlines()
                        if line.lstrip().startswith(".")
                    ]
                    asc.write_text(
                        "Version 4\n"
                        + "\n".join(f"TEXT 0 0 Left 2 !{line}" for line in directives)
                        + "\n",
                        encoding="utf-8",
                    )
                    return weave.subprocess.CompletedProcess(command, 0, "converted\n", "")
                return weave.subprocess.CompletedProcess(command, 0, "MATCH\n", "")

            smoke = {
                "ok": True, "input": str(asc), "raw": str(result.parent / "filter-asc.raw"),
                "log": str(result.parent / "filter-asc.log"), "errors": [],
            }
            with patch.object(weave, "run", side_effect=fake_run), patch.object(
                weave, "run_simulation", return_value=smoke
            ):
                code = weave.main([
                    "--net", str(canonical), "--weave-dir", str(weave_dir), "--node", sys.executable,
                    "--ltspice", str(ltspice), "--asc", str(asc), "--result", str(result), "--force",
                ])
            self.assertEqual(code, 0)
            self.assertNotIn(".save", asc.read_text(encoding="utf-8").lower())

    def test_default_asc_path_uses_delivery_root_for_support_net(self):
        net = Path(r"C:\circuits\filter_files\filter.net")
        self.assertEqual(
            weave.default_asc_path(net),
            Path(r"C:\circuits\filter.asc"),
        )

    def test_rewrites_dependency_directive_for_root_level_asc(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support = root / "filter_files"
            support.mkdir()
            dependency = support / "deps" / "001_device.lib"
            dependency.parent.mkdir()
            dependency.write_text(".subckt DEVICE in out 0\n.ends DEVICE\n", encoding="utf-8")
            net = support / "filter.net"
            asc = root / "filter.asc"
            rewritten, changed = weave.rewrite_asc_dependency_paths(
                "Version 4\nTEXT 0 100 Left 2 !.include deps/001_device.lib\n",
                net,
                asc,
            )
            self.assertTrue(changed)
            self.assertIn("!.include filter_files/deps/001_device.lib", rewritten)

    def test_conversion_accepts_separate_asc_and_result_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support = root / "filter_files"
            support.mkdir()
            net = support / "filter.net"
            net.write_text("R1 in out 1k\nV1 in 0 1\n.end\n", encoding="utf-8")
            weave_dir = root / "weave"
            weave_dir.mkdir()
            (weave_dir / "weave.js").write_text("// test stub\n", encoding="utf-8")
            ltspice = root / "LTspice.exe"
            ltspice.write_bytes(b"stub")
            asc = root / "filter.asc"
            result = support / "filter.weave-verification.txt"

            def fake_run(command, _cwd):
                if "convert" in command:
                    asc.write_bytes(b"Version 4\nSHEET 1 1200 800\n")
                    return weave.subprocess.CompletedProcess(command, 0, "converted\n", "")
                return weave.subprocess.CompletedProcess(command, 0, "MATCH\n", "")

            smoke = {
                "ok": True, "input": str(asc), "raw": str(support / "filter-asc.raw"),
                "log": str(support / "filter-asc.log"), "errors": [],
            }
            with patch.object(weave, "run", side_effect=fake_run), patch.object(weave, "run_simulation", return_value=smoke) as smoke_runner:
                code = weave.main([
                    "--net", str(net), "--weave-dir", str(weave_dir), "--node", sys.executable,
                    "--ltspice", str(ltspice), "--asc", str(asc), "--result", str(result), "--force",
                ])
            self.assertEqual(code, 0)
            self.assertTrue(asc.is_file())
            text = result.read_text(encoding="utf-8")
            self.assertIn("VERDICT=MATCH", text)
            self.assertIn("ASC_SMOKE=PASS", text)
            call = smoke_runner.call_args
            self.assertEqual(call.kwargs["output_dir"].resolve(), support.resolve())

    def test_smoke_failure_rejects_connectivity_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            support = root / "filter_files"
            support.mkdir()
            net = support / "filter.net"
            net.write_text("V1 in 0 1\nR1 in out 1k\n.end\n", encoding="utf-8")
            weave_dir = root / "weave"
            weave_dir.mkdir()
            (weave_dir / "weave.js").write_text("// test stub\n", encoding="utf-8")
            ltspice = root / "LTspice.exe"
            ltspice.write_bytes(b"stub")
            asc = root / "filter.asc"
            result = support / "filter.weave-verification.txt"

            def fake_run(command, _cwd):
                if "convert" in command:
                    asc.write_bytes(b"Version 4\n")
                    return weave.subprocess.CompletedProcess(command, 0, "converted\n", "")
                return weave.subprocess.CompletedProcess(command, 0, "MATCH\n", "")

            smoke = {
                "ok": False, "input": str(asc), "raw": str(support / "filter-asc.raw"),
                "log": str(support / "filter-asc.log"), "errors": ["No such node"],
            }
            with patch.object(weave, "run", side_effect=fake_run), patch.object(weave, "run_simulation", return_value=smoke):
                code = weave.main([
                    "--net", str(net), "--weave-dir", str(weave_dir), "--node", sys.executable,
                    "--ltspice", str(ltspice), "--asc", str(asc), "--result", str(result), "--force",
                ])
            self.assertEqual(code, 1)
            text = result.read_text(encoding="utf-8")
            self.assertIn("WEAVE_VERDICT=MATCH", text)
            self.assertIn("ASC_SMOKE=FAIL", text)
            self.assertIn("VERDICT=ASC_SMOKE_FAILED", text)


if __name__ == "__main__":
    unittest.main()
