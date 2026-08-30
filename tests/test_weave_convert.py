import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import weave_convert as weave  # noqa: E402


class WeaveArtifactLayoutTests(unittest.TestCase):
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
            asc = root / "filter.asc"
            result = support / "filter.weave-verification.txt"

            def fake_run(command, _cwd):
                if "convert" in command:
                    asc.write_bytes(b"Version 4\nSHEET 1 1200 800\n")
                    return weave.subprocess.CompletedProcess(command, 0, "converted\n", "")
                return weave.subprocess.CompletedProcess(command, 0, "MATCH\n", "")

            with patch.object(weave, "run", side_effect=fake_run):
                code = weave.main([
                    "--net", str(net), "--weave-dir", str(weave_dir), "--node", sys.executable,
                    "--asc", str(asc), "--result", str(result), "--force",
                ])
            self.assertEqual(code, 0)
            self.assertTrue(asc.is_file())
            self.assertIn("VERDICT=MATCH", result.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
