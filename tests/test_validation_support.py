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
    parameter_values,
    parse_parameters,
    stage_net_with_dependencies,
    simulation_evidence_payload,
    simulation_evidence_key,
)


class ValidationSupportTests(unittest.TestCase):
    def test_combined_param_line_parses_all_assignments(self) -> None:
        text = ".param R=10k C=100n L={R*C} ; editable values\n.end\n"
        assignments, errors = parse_parameters(text)
        self.assertEqual(errors, [])
        self.assertEqual([item["name"] for item in assignments], ["R", "C", "L"])
        values, value_errors = parameter_values(text)
        self.assertEqual(value_errors, [])
        self.assertEqual(values["r"]["value"], "10k")
        self.assertEqual(values["c"]["value"], "100n")

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
            simulation_input = {"dependencies": {"reuse_allowed": True}}
            store.record_success("key", raw=raw, log=log, run_report=None, result=result,
                                 simulation_input=simulation_input)
            self.assertIsNotNone(store.reuse("key", root / "copy.raw", root / "copy.log",
                                              simulation_input=simulation_input))
            raw.write_bytes(b"changed")
            self.assertIsNone(store.reuse("key", root / "copy2.raw", root / "copy2.log",
                                          simulation_input=simulation_input))

            unbound = EvidenceStore(root / "unbound.json")
            raw.write_bytes(b"binary-raw")
            unbound.record_success("key", raw=raw, log=log, run_report=None, result=result)
            self.assertIsNone(unbound.reuse("key", root / "copy3.raw", root / "copy3.log",
                                            simulation_input=simulation_input))

    def test_evidence_identity_is_analysis_specific(self) -> None:
        common = {
            "rendered_text": "R1 in out 1k\n.ac dec 10 10 10k\n.end\n",
            "analysis": {"kind": "ac", "directive": ".ac dec 10 10 10k"},
            "params": {},
            "dependencies": {"version": 1, "files": []},
            "executable": Path("LTspice.exe"),
        }
        first = simulation_evidence_key(source_net_sha256="first", **common)
        same_rendered = simulation_evidence_key(source_net_sha256="second", **common)
        changed_rendered = simulation_evidence_key(
            source_net_sha256="second",
            rendered_text=common["rendered_text"].replace("10k", "20k"),
            analysis=common["analysis"],
            params=common["params"],
            dependencies=common["dependencies"],
            executable=common["executable"],
        )
        self.assertEqual(first, same_rendered)
        self.assertNotEqual(first, changed_rendered)

    def test_evidence_fingerprint_uses_the_normal_batch_flags(self) -> None:
        payload = simulation_evidence_payload(
            source_net_sha256="source",
            rendered_text="V1 in 0 1\n.op\n.end\n",
            analysis={"kind": "op", "directive": ".op"},
            params={},
            dependencies={"reuse_allowed": True, "files": []},
            executable=Path("LTspice.exe"),
        )
        self.assertEqual(payload["settings"]["flags"], ["-b", "-Run"])

    def test_dependency_scope_marks_search_path_content_unverified_and_disables_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            root.mkdir()
            local = root / "local.lib"
            local.write_text(".model DLOCAL D\n", encoding="utf-8")
            external_dir = Path(folder) / "external"
            external_dir.mkdir()
            external = external_dir / "external.lib"
            external.write_text(".model DEXT D\n", encoding="utf-8")
            net = root / "circuit.net"
            text = (
                ".include local.lib\n"
                f".include {external}\n"
                ".include search_only.lib\n"
                ".end\n"
            )
            net.write_text(text, encoding="utf-8")
            manifest = dependency_manifest(net, text)
            scopes = {item["requested"]: item["scope"] for item in manifest["files"]}
            verified = {item["requested"]: item["content_verified"] for item in manifest["files"]}
            self.assertEqual(scopes["local.lib"], "local")
            self.assertEqual(scopes[str(external)], "external")
            self.assertEqual(scopes["search_only.lib"], "search_path")
            self.assertTrue(verified["local.lib"])
            self.assertTrue(verified[str(external)])
            self.assertFalse(verified["search_only.lib"])
            self.assertTrue(manifest["ok"])
            self.assertFalse(manifest["reuse_allowed"])

            raw = root / "result.raw"
            log = root / "result.log"
            raw.write_bytes(b"raw")
            log.write_text("Simulation successful\n", encoding="utf-8")
            store = EvidenceStore(root / "evidence.json")
            input_data = {"dependencies": manifest}
            store.record_success("key", raw=raw, log=log, run_report=None,
                                 result={"ok": True, "fresh_raw": True, "fresh_log": True},
                                 simulation_input=input_data)
            self.assertIsNone(store.reuse("key", root / "copy.raw", root / "copy.log", simulation_input=input_data))


if __name__ == "__main__":
    unittest.main()
