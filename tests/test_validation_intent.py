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

    def test_relative_paths_resolve_from_config_and_net(self):
        temp, root, net, config = self._environment()
        with temp:
            paths = intent.resolve_paths("source/circuit.net", config, cwd=root)
        self.assertEqual(paths["net"], net.resolve())
        self.assertEqual(paths["output"], (root / "out" / "circuit").resolve())
        self.assertEqual(paths["ltspice"], (root / "LTspice.exe").resolve())
        self.assertEqual(paths["config"], config.resolve())

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
