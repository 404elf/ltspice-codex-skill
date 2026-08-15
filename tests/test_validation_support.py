from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validation_support import (  # noqa: E402
    EvidenceStore,
    dependency_manifest,
    stage_net_with_dependencies,
)


class ValidationSupportTests(unittest.TestCase):
    def test_relative_dependencies_are_manifested_and_staged(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model_dir = root / "models"
            model_dir.mkdir()
            model = model_dir / "device.lib"
            model.write_text(".model DTEST D(Is=1n)\n", encoding="utf-8")
            net = root / "circuit.net"
            text = ".include models/device.lib\nD1 out 0 DTEST\n.end\n"
            net.write_text(text, encoding="utf-8")
            manifest = dependency_manifest(net, text)
            self.assertTrue(manifest["ok"])
            stage = root / "stage"
            staged_net = stage_net_with_dependencies(net, text, stage, manifest)
            self.assertTrue(staged_net.is_file())
            self.assertIn("deps/", staged_net.read_text(encoding="utf-8"))
            self.assertTrue(list((stage / "deps").glob("*.lib")))

    def test_evidence_reuse_requires_hash_bound_fresh_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            raw = root / "circuit.raw"
            log = root / "circuit.log"
            raw.write_bytes(b"binary-raw")
            log.write_text("Simulation successful\n", encoding="utf-8")
            store = EvidenceStore(root / "simulation_evidence.json")
            result = {"ok": True, "fresh_raw": True, "fresh_log": True, "elapsed_seconds": 1.0}
            store.record_success("key", raw=raw, log=log, run_report=None, result=result)
            self.assertIsNotNone(store.reuse("key", root / "copy.raw", root / "copy.log"))
            raw.write_bytes(b"changed")
            self.assertIsNone(store.reuse("key", root / "copy2.raw", root / "copy2.log"))


if __name__ == "__main__":
    unittest.main()
