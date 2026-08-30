import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_validation_intent as intent  # noqa: E402


class ValidationIntentTests(unittest.TestCase):
    def _environment(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "config").mkdir()
        (root / "source").mkdir()
        (root / "out").mkdir()
        net = root / "source" / "circuit.net"
        net.write_text("V1 in 0 AC 1\nR1 in out 1k\nC1 out 0 100n\n.ac dec 100 1 100k\n.end\n", encoding="utf-8")
        ltspice = root / "LTspice.exe"
        ltspice.write_text("test", encoding="utf-8")
        config = root / "config" / ".ltspice-codex-config.json"
        config.write_text(json.dumps({
            "python": sys.executable,
            "ltspice": "../LTspice.exe",
            "output_root": "../out",
        }), encoding="utf-8")
        return temp, root, net, config

    def test_minimal_intent_normalizes_to_canonical_spec(self):
        normalized = intent.normalize_intent({
            "mode": "standard",
            "analyses": {"ac": {}},
            "requirements": [{
                "name": "cutoff", "analysis": "ac", "measure": "cutoff",
                "signal": "V(out)", "reference": "V(in)", "target": 1591, "tolerance": "10%",
            }],
        })
        self.assertEqual(normalized["mode"], "STANDARD")
        self.assertEqual(normalized["spec"]["analyses"], [{"name": "ac", "kind": "ac"}])
        self.assertEqual(normalized["spec"]["metrics"]["cutoff"]["kind"], "fc_3db")
        self.assertEqual(normalized["spec"]["metrics"]["cutoff"]["tolerance_percent"], 10.0)
        self.assertTrue(normalized["spec"]["preflight"])

    def test_mechanically_recoverable_input_normalizes(self):
        parsed = intent.parse_intent_text(
            "// comment\n"
            "{'mode':'STANDARD','analysis':{'op':'.op'},"
            "'metrics':{'bias':{'kind':'value','trace':'V(in)','analysis':'op',}},}"
        )
        normalized = intent.normalize_intent(parsed)
        self.assertEqual(normalized["spec"]["analyses"][0]["kind"], "op")
        self.assertEqual(normalized["spec"]["metrics"]["bias"]["kind"], "final")

    def test_op_axis_metric_is_rejected_before_suite(self):
        with self.assertRaises(intent.IntentError):
            intent.normalize_intent({
                "analyses": {"op": ".op"},
                "requirements": [{
                    "name": "bad_axis", "measure": "value_at", "trace": "V(in)",
                    "analysis": "op", "at": 1,
                }],
            })

    def test_ambiguous_alias_is_rejected(self):
        with self.assertRaises(intent.IntentError):
            intent.normalize_intent({"analyses": {"op": ".op"}, "analysis": {"op": ".op"}})

    def test_nested_requirements_and_tolerances_route_by_analysis(self):
        normalized = intent.normalize_intent({
            "analyses": {
                "ac": {
                    "directive": ".ac dec 10 10 1000",
                    "requirements": {
                        "gain": {
                            "measure": "gain_at",
                            "signal": "V(out)",
                            "reference": "V(in)",
                            "at": 1000,
                            "target": 1,
                        }
                    },
                    "tolerances": {"parameters": {"R": 5}},
                },
                "tran": {
                    "directive": ".tran 0 1m",
                    "requirements": [{
                        "name": "peak",
                        "measure": "abs_max",
                        "signal": "V(out)",
                        "max": 2,
                    }],
                    "tolerances": {"parameters": {"C": 10}},
                },
            }
        })
        spec = normalized["spec"]
        self.assertEqual(spec["metrics"]["gain"]["analysis"], "ac")
        self.assertEqual(spec["metrics"]["peak"]["analysis"], "tran")
        self.assertEqual(
            {group["analysis"] for group in spec["tolerance_groups"]},
            {"ac", "tran"},
        )

    def test_model_policy_is_canonicalized(self):
        normalized = intent.normalize_intent({
            "analyses": {"op": ".op"},
            "model_policy": {"policy": "real_device_required"},
        })
        self.assertEqual(normalized["spec"]["model_policy"], "real_device_required")
        with self.assertRaises(intent.IntentError):
            intent.normalize_intent({
                "analyses": {"op": ".op"},
                "model_policy": "unknown",
            })

    def test_common_intent_aliases_and_safe_field_normalization(self):
        normalized = intent.normalize_intent({
            "validation": {
                "validation_mode": "standard",
                "analysis_plan": {
                    "ac": {"type": "ac", "command": ".ac dec 10 10 10k"},
                },
                "checks": [{
                    "id": "gain",
                    "metric": "gain",
                    "node": "V(out)",
                    "reference_trace": "V(in)",
                    "frequency": 1000,
                    "expected": 1,
                    "tol": "5%",
                }],
            }
        })
        metric = normalized["spec"]["metrics"]["gain"]
        self.assertEqual(normalized["mode"], "STANDARD")
        self.assertEqual(metric["kind"], "gain_at")
        self.assertEqual(metric["trace"], "V(out)")
        self.assertEqual(metric["reference"], "V(in)")
        self.assertEqual(metric["x"], 1000)
        self.assertEqual(metric["target"], 1)
        self.assertEqual(metric["tolerance_percent"], 5.0)

    def test_grouped_tolerance_forms_share_one_canonical_representation(self):
        analyses = {"ac": ".ac dec 10 10 10k", "tran": ".tran 0 1m"}
        forms = [
            {"tolerances": {"ac": {"parameters": {"R": 5}}, "tran": {"params": {"C": "10%"}}}},
            {"tolerance_groups": [
                {"analysis": "ac", "parameters": {"R": {"percent": 5}}},
                {"analysis": "tran", "corners": {"C": [-10, 10]}},
            ]},
            {"tolerances": {"groups": {
                "ac": {"R": 5}, "tran": {"parameters": {"C": {"low": -10, "high": 10}}},
            }}},
        ]
        canonical = []
        for form in forms:
            intent_spec = {"analyses": analyses, **form}
            canonical.append(intent.normalize_intent(intent_spec)["spec"]["tolerance_groups"])
        self.assertEqual(canonical[0], canonical[1])
        self.assertEqual(canonical[1], canonical[2])

    def test_entrypoint_aliases_build_an_absolute_suite_command(self):
        temp, root, net, config = self._environment()
        intent_file = root / "intent.json"
        intent_file.write_text('{"analyses":{"ac":".ac dec 10 10 10k"}}', encoding="utf-8")
        completed = intent.subprocess.CompletedProcess([], 0, '{"status":"PASS","ok":true}', "")
        output = io.StringIO()
        with temp, patch.object(intent.subprocess, "run", return_value=completed) as run, contextlib.redirect_stdout(output):
            code = intent.main([
                "--netlist", str(net), "--intent-file", str(intent_file), "--config-file", str(config),
            ])
        self.assertEqual(code, 0)
        command = run.call_args.args[0]
        self.assertTrue(Path(command[1]).is_absolute())
        self.assertTrue(command[1].endswith("run_validation_suite.py"))

    def test_relative_paths_resolve_from_config_and_net(self):
        temp, root, net, config = self._environment()
        with temp:
            paths = intent.resolve_paths("source/circuit.net", config, cwd=root)
        self.assertEqual(paths["net"], net.resolve())
        self.assertEqual(paths["output"], (root / "out" / "circuit").resolve())
        self.assertEqual(paths["support"], (root / "out" / "circuit" / "circuit_files").resolve())
        self.assertEqual(paths["canonical_net"], (root / "out" / "circuit" / "circuit_files" / "circuit.net").resolve())
        self.assertEqual(paths["asc"], (root / "out" / "circuit" / "circuit.asc").resolve())
        self.assertEqual(paths["weave_result"], (root / "out" / "circuit" / "circuit_files" / "circuit.weave-verification.txt").resolve())
        self.assertEqual(paths["ltspice"], (root / "LTspice.exe").resolve())
        self.assertEqual(paths["config"], config.resolve())

    def test_entrypoint_promotes_net_and_routes_runtime_artifacts_to_support(self):
        temp, root, net, config = self._environment()
        intent_file = root / "intent.json"
        intent_file.write_text('{"analyses":{"ac":".ac dec 10 10 10k"}}', encoding="utf-8")
        completed = intent.subprocess.CompletedProcess([], 0, '{"status":"PASS","ok":true,"ltspice_runs":1}\n', "")
        output = io.StringIO()
        with temp, patch.object(intent.subprocess, "run", return_value=completed) as run, contextlib.redirect_stdout(output):
            code = intent.main(["--net", str(net), "--intent", str(intent_file), "--config", str(config)])
            self.assertEqual(code, 0)
            delivery = root / "out" / "circuit"
            support = delivery / "circuit_files"
            canonical = support / "circuit.net"
            self.assertTrue(canonical.is_file())
            self.assertEqual(canonical.read_bytes(), net.read_bytes())
            command = run.call_args.args[0]
            self.assertIn(str(support.resolve()), command)
            self.assertEqual(command[command.index("--net") + 1], str(canonical.resolve()))
            self.assertEqual(command[command.index("--output") + 1], str(support.resolve()))
            result = json.loads(output.getvalue())
            self.assertEqual(result["output_directory"], str(delivery.resolve()))
            self.assertEqual(result["support_directory"], str(support.resolve()))
            self.assertEqual(result["canonical_net"], str(canonical.resolve()))
            self.assertEqual(result["expected_asc"], str((delivery / "circuit.asc").resolve()))

    def test_readable_model_dependencies_are_staged_under_support(self):
        temp, root, net, config = self._environment()
        model = root / "source" / "device.lib"
        model.write_text(".subckt DEVICE in out 0\nRmodel in out 1k\n.ends DEVICE\n", encoding="utf-8")
        net.write_text(
            ".include device.lib\n"
            "V1 in 0 AC 1\n"
            "X1 in out 0 DEVICE\n"
            ".ac dec 10 10 10k\n.end\n",
            encoding="utf-8",
        )
        paths = intent.resolve_paths(str(net), config)
        with temp:
            canonical = intent.prepare_canonical_net(paths)
            support = root / "out" / "circuit" / "circuit_files"
            self.assertEqual(canonical, (support / "circuit.net").resolve())
            self.assertIn(".include deps/001_device.lib", canonical.read_text(encoding="utf-8"))
            self.assertTrue((support / "deps" / "001_device.lib").is_file())

    def test_invalid_intent_fails_before_subprocess(self):
        temp, root, net, config = self._environment()
        bad_intent = root / "bad.json"
        bad_intent.write_text(json.dumps({"requirements": [], "unexpected": True}), encoding="utf-8")
        output = io.StringIO()
        with temp, patch.object(intent.subprocess, "run") as run, contextlib.redirect_stdout(output):
            code = intent.main(["--net", str(net), "--intent", str(bad_intent), "--config", str(config)])
        self.assertEqual(code, 2)
        run.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["stage"], "intent")
        self.assertEqual(result["ltspice_calls"], 0)

    def test_missing_net_fails_before_subprocess(self):
        temp, root, _net, config = self._environment()
        missing = root / "missing.net"
        intent_file = root / "intent.json"
        intent_file.write_text("{}", encoding="utf-8")
        output = io.StringIO()
        with temp, patch.object(intent.subprocess, "run") as run, contextlib.redirect_stdout(output):
            code = intent.main(["--net", str(missing), "--intent", str(intent_file), "--config", str(config)])
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["stage"], "intent")

    def test_canonical_spec_matches_existing_suite_shape(self):
        normalized = intent.normalize_intent({
            "mode": "QUICK",
            "analyses": {"ac_response": {"kind": "ac", "directive": ".ac dec 200 10 100k"}},
            "requirements": [{
                "name": "gain", "analysis": "ac_response", "measure": "gain_at",
                "signal": "V(out)", "reference": "V(in)", "at": 1000,
                "target": 2, "tolerance": 5,
            }],
            "required_nets": ["in", "out"],
        })
        self.assertEqual(normalized["spec"], {
            "preflight": True,
            "simulation_fail_fast": True,
            "analyses": [{"name": "ac_response", "kind": "ac", "directive": ".ac dec 200 10 100k"}],
            "metrics": {
                "gain": {
                    "kind": "gain_at", "trace": "V(out)", "analysis": "ac_response",
                    "reference": "V(in)", "x": 1000, "target": 2, "tolerance_percent": 5.0,
                },
            },
            "required_nets": ["in", "out"],
        })

    def test_suite_command_is_the_only_runtime_boundary(self):
        temp, root, net, config = self._environment()
        intent_file = root / "intent.json"
        intent_file.write_text(json.dumps({
            "mode": "STANDARD",
            "analyses": {"ac": {}},
            "requirements": [{"name": "gain", "analysis": "ac", "measure": "gain_at",
                               "signal": "V(out)", "reference": "V(in)", "at": 1000}],
        }), encoding="utf-8")
        completed = intent.subprocess.CompletedProcess([], 0, '{"status":"PASS","ok":true,"ltspice_runs":1}\n', "")
        output = io.StringIO()
        with temp, patch.object(intent.subprocess, "run", return_value=completed) as run, contextlib.redirect_stdout(output):
            code = intent.main(["--net", str(net), "--intent", str(intent_file), "--config", str(config)])
        self.assertEqual(code, 0)
        command = run.call_args.args[0]
        self.assertIn("run_validation_suite.py", command[1])
        self.assertNotIn("--ltspice-executable", command)
        self.assertEqual(command.count("--net"), 1)
        self.assertEqual(command.count("--spec"), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["ltspice_calls"], 1)
        self.assertIn("validation_summary.json", result["summary_path"])


if __name__ == "__main__":
    unittest.main()
