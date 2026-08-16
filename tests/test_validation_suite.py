from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_validation_suite as suite  # noqa: E402
from run_validation_suite import (  # noqa: E402
    check_metric,
    can_use_exact_source,
    coalesce_analyses,
    dry_run_spec,
    evaluate_metrics,
    expand_corners,
    expand_tolerance_groups,
    preflight_result_ok,
    render_analysis_net,
    replace_parameters,
    validate_model_policy,
)


class ValidationSuiteUnitTests(unittest.TestCase):
    def test_parameter_replacement_updates_existing_value_and_adds_new_value(self) -> None:
        source = ".param R=10k\nV1 in 0 1\n.end\n"
        updated = replace_parameters(source, {"R": "12k", "C": "1n"})
        self.assertIn(".param R=12k", updated)
        self.assertIn(".param C=1n", updated)

    def test_analysis_render_replaces_source_directives(self) -> None:
        source = ".tran 0 1m\n.ac dec 10 10 10k\n.end\n"
        rendered = render_analysis_net(source, {"name": "ac", "kind": "ac", "directive": ".ac dec 40 10 100k"}, {})
        self.assertNotIn(".tran", rendered)
        self.assertIn(".ac dec 40 10 100k", rendered)

    def test_percentage_corners_expand_deterministically(self) -> None:
        source = ".param R=10k\n.param C=1n\n.end\n"
        corners = expand_corners(source, {"R": [-5, 5], "C": [-10, 10]})
        self.assertEqual(len(corners), 4)
        self.assertEqual(corners[0]["params"]["R"], 9500.0)
        self.assertAlmostEqual(corners[0]["params"]["C"], 0.9e-9)

    def test_tolerance_groups_route_corners_to_the_declared_analysis(self) -> None:
        source = ".param R=1k C=100n\n.tran 0 1m\n.ac dec 10 10 10k\n.end\n"
        corners = expand_tolerance_groups(source, [
            {"analysis": "ac", "corners": {"R": [-5, 5]}},
            {"analysis": "tran", "corners": {"C": [-10, 10]}},
        ])
        self.assertEqual(len(corners), 4)
        self.assertEqual({item["analysis"] for item in corners}, {"ac", "tran"})

    def test_real_device_policy_rejects_known_generic_model(self) -> None:
        errors = validate_model_policy(
            "XU1 in 0 vdd vss out UniversalOpAmp2\n.op\n.end\n",
            "real_device_required",
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("generic model UniversalOpAmp2 detected", errors[0])

    def test_metric_bounds_are_enforced(self) -> None:
        self.assertEqual(check_metric(5.0, {"min": 4, "max": 6}), (True, None))
        ok, reason = check_metric(7.0, {"max": 6})
        self.assertFalse(ok)
        self.assertIn("> max", reason or "")

    def test_negative_target_tolerance_has_ordered_bounds(self) -> None:
        self.assertEqual(check_metric(-110.0, {"target": -100.0, "tolerance_percent": 10}), (True, None))
        ok, reason = check_metric(-111.0, {"target": -100.0, "tolerance_percent": 10})
        self.assertFalse(ok)
        self.assertIn("outside target band", reason or "")

    def test_preflight_failure_is_not_a_passing_gate(self) -> None:
        self.assertFalse(preflight_result_ok({"ok": False, "exit_code": 1}))
        self.assertTrue(preflight_result_ok({"ok": True, "exit_code": 0}))

    def test_original_net_is_not_used_as_exact_job_for_multiple_analyses(self) -> None:
        source = [("tran", ".tran 0 1m"), ("ac", ".ac dec 10 10 10k")]
        self.assertFalse(can_use_exact_source(source, {"kind": "tran"}))
        self.assertTrue(can_use_exact_source([source[0]], {"kind": "tran"}))

    def test_combined_param_line_is_replaced_reliably(self) -> None:
        source = ".param R=10k C=100n\nR1 in out {R}\nC1 out 0 {C}\n.tran 0 1m\n.end\n"
        updated = replace_parameters(source, {"R": "12k", "C": "120n"})
        self.assertIn(".param R=12k C=120n", updated)

    def test_dry_run_allows_scalar_op_metrics_and_rejects_axis_metrics(self) -> None:
        net = ".param R=1k\nV1 in 0 1\nR1 in out {R}\nR2 out 0 1k\n.op\n.end\n"
        scalar = {"analyses": [{"name": "op", "kind": "op"}], "metrics": {
            "out": {"analysis": "op", "trace": "V(out)", "kind": "value"},
        }}
        self.assertTrue(dry_run_spec(net, Path("op.net"), scalar)["ok"])
        axis = {"analyses": [{"name": "op", "kind": "op"}], "metrics": {
            "out": {"analysis": "op", "trace": "V(out)", "kind": "value_at", "x": 1},
        }}
        report = dry_run_spec(net, Path("op.net"), axis)
        self.assertFalse(report["ok"])
        self.assertTrue(any("needs an analysis axis" in item for item in report["errors"]))

    def test_dry_run_allows_ltspice_search_path_dependency_but_marks_it_unverified(self) -> None:
        net = ".lib LTC.lib\nV1 in 0 1\n.op\n.end\n"
        spec = {"analyses": [{"name": "op", "kind": "op"}]}
        report = dry_run_spec(net, Path("op.net"), spec)
        self.assertTrue(report["ok"])
        self.assertTrue(any("search_path" in item for item in report["warnings"]))
        self.assertFalse(report["dependency_manifest"]["reuse_allowed"])

    def test_dry_run_rejects_invalid_dc_and_duplicate_parameters(self) -> None:
        net = ".param R=1k\n.param R=2k\nV1 in 0 1\nR1 in 0 {R}\n.dc V1 1 1 1\n.end\n"
        spec = {"analyses": [{"name": "dc", "kind": "dc"}]}
        report = dry_run_spec(net, Path("invalid.net"), spec)
        self.assertFalse(report["ok"])
        self.assertTrue(any("duplicate .param" in item for item in report["errors"]))
        self.assertTrue(any("start=stop" in item for item in report["errors"]))

    def test_dry_run_rejects_invalid_metric_numbers(self) -> None:
        net = ".param R=1k\nV1 in 0 1\nR1 in 0 {R}\n.ac dec 10 10 10k\n.end\n"
        spec = {"analyses": [{"name": "ac", "kind": "ac"}], "metrics": {
            "gain": {"analysis": "ac", "trace": "V(in)", "kind": "value_at", "x": "not-a-number", "min": 2, "max": 1},
        }}
        report = dry_run_spec(net, Path("invalid-metric.net"), spec)
        self.assertFalse(report["ok"])
        self.assertTrue(any("x must be numeric" in item for item in report["errors"]))
        self.assertTrue(any("min must not exceed max" in item for item in report["errors"]))

    def test_monotonic_corner_plan_reduces_two_parameters_to_two_endpoints(self) -> None:
        source = ".param R=1k C=100n\n.tran 0 1m\n.end\n"
        cartesian = expand_corners(source, {"R": [-10, 10], "C": [-10, 10]})
        reduced = expand_corners(
            source,
            {"R": [-10, 10], "C": [-10, 10]},
            strategy="monotonic",
            monotonic={"fc": {"R": "inverse", "C": "inverse"}},
        )
        self.assertEqual(len(cartesian), 4)
        self.assertEqual(len(reduced), 2)

    def test_duplicate_analysis_directives_are_coalesced(self) -> None:
        analyses = [
            {"name": "ac-main", "kind": "ac", "directive": ".ac dec 10 10 10k"},
            {"name": "ac-cutoff", "kind": "ac", "directive": ".ac dec 10 10 10k"},
        ]
        grouped = coalesce_analyses(analyses, [])
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["aliases"], ["ac-main", "ac-cutoff"])

    def test_normal_suite_runs_dry_run_and_dry_run_flag_stays_simulation_free(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            net = root / "circuit.net"
            spec = root / "circuit.json"
            net.write_text("V1 in 0 1\n.op\n.end\n", encoding="utf-8")
            spec.write_text(json.dumps({"analyses": [{"name": "op", "kind": "op"}]}), encoding="utf-8")
            fake_result = {
                "ok": True, "returncode": 0, "fresh_raw": True, "fresh_log": True,
                "errors": [], "elapsed_seconds": 0.01,
            }
            with patch.object(suite, "run_simulation", return_value=fake_result) as runner:
                with patch.object(sys, "argv", ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(root / "normal")]):
                    self.assertEqual(suite.main(), 0)
                self.assertEqual(runner.call_count, 1)

                runner.reset_mock()
                with patch.object(sys, "argv", ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(root / "dry"), "--dry-run"]):
                    self.assertEqual(suite.main(), 0)
                runner.assert_not_called()

            summary = json.loads((root / "dry" / "validation_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["dry_run"]["ok"])
            self.assertTrue(summary["dry_run_only"])

    def test_metric_only_changes_reuse_evidence_without_an_extra_ltspice_call(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            net = root / "circuit.net"
            spec = root / "circuit.json"
            output = root / "output"
            net.write_text("V1 in 0 1\n.op\n.end\n", encoding="utf-8")

            def write_spec(target: float) -> None:
                spec.write_text(json.dumps({
                    "analyses": [{"name": "op", "kind": "op"}],
                    "metrics": {"out": {
                        "analysis": "op", "trace": "V(out)", "kind": "value",
                        "target": target, "tolerance_percent": 0,
                    }},
                }), encoding="utf-8")

            def fake_run(job_net: Path, _executable: Path, report_path: Path, **_kwargs: object) -> dict[str, object]:
                raw = job_net.with_suffix(".raw")
                log = job_net.with_suffix(".log")
                raw.write_bytes(b"raw-evidence")
                log.write_text("Simulation successful\n", encoding="utf-8")
                report_path.write_text("{}\n", encoding="utf-8")
                return {
                    "ok": True, "returncode": 0, "fresh_raw": True, "fresh_log": True,
                    "errors": [], "elapsed_seconds": 0.01, "raw": str(raw),
                    "log": str(log), "run_input": str(job_net),
                }

            write_spec(1.0)
            with patch.object(suite, "run_simulation", side_effect=fake_run) as runner:
                with patch.object(suite, "raw_arrays", return_value=(np.array([0.0]), {"V(out)": np.array([1.0])})):
                    with patch.object(sys, "argv", ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(output)]):
                        self.assertEqual(suite.main(), 0)

                    write_spec(2.0)
                    with patch.object(sys, "argv", ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(output)]):
                        self.assertEqual(suite.main(), 1)

                    write_spec(1.0)
                    with patch.object(sys, "argv", ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(output)]):
                        self.assertEqual(suite.main(), 0)

                self.assertEqual(runner.call_count, 1)

            summary = json.loads((output / "validation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["ltspice_runs"], 0)
            self.assertGreaterEqual(summary["evidence_reused"], 1)

    def test_stdout_is_compact_by_default_and_verbose_json_is_opt_in(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            net = root / "circuit.net"
            spec = root / "circuit.json"
            net.write_text("V1 in 0 1\n.op\n.end\n", encoding="utf-8")
            spec.write_text(json.dumps({"analyses": [{"name": "op", "kind": "op"}]}), encoding="utf-8")

            def invoke(output: Path, *extra: str) -> dict[str, object]:
                stdout = io.StringIO()
                argv = ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(output), "--dry-run", *extra]
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
                    self.assertEqual(suite.main(), 0)
                return json.loads(stdout.getvalue())

            compact = invoke(root / "compact")
            self.assertNotIn("schema_version", compact)
            self.assertIn("status", compact)
            verbose = invoke(root / "verbose", "--verbose-json")
            self.assertEqual(verbose["schema_version"], 2)
            self.assertIn("dry_run", verbose)

    def test_invalid_spec_in_normal_path_fails_before_any_ltspice_call(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            net = root / "circuit.net"
            spec = root / "circuit.json"
            output = root / "output"
            net.write_text("V1 in 0 1\n.dc V1 1 1 1\n.end\n", encoding="utf-8")
            spec.write_text(json.dumps({"analyses": [{"name": "dc", "kind": "dc"}]}), encoding="utf-8")
            stdout = io.StringIO()
            with patch.object(suite, "run_simulation") as runner:
                argv = ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(output)]
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
                    self.assertEqual(suite.main(), 1)
                runner.assert_not_called()
            compact = json.loads(stdout.getvalue())
            self.assertEqual(compact["ltspice_runs"], 0)
            self.assertEqual(compact["status"], "FAIL")
            summary = json.loads((output / "validation_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(any("start=stop" in item for item in summary["failures"]))

    def test_generic_model_policy_fails_before_any_ltspice_call(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            net = root / "circuit.net"
            spec = root / "circuit.json"
            output = root / "output"
            net.write_text("XU1 in 0 vdd vss out UniversalOpAmp2\n.op\n.end\n", encoding="utf-8")
            spec.write_text(json.dumps({
                "analyses": [{"name": "op", "kind": "op"}],
                "model_policy": "real_device_required",
            }), encoding="utf-8")
            stdout = io.StringIO()
            with patch.object(suite, "run_simulation") as runner:
                argv = ["run_validation_suite.py", "--net", str(net), "--spec", str(spec),
                        "--ltspice", str(root / "LTspice.exe"), "--output", str(output)]
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
                    self.assertEqual(suite.main(), 1)
                runner.assert_not_called()
            compact = json.loads(stdout.getvalue())
            self.assertEqual(compact["ltspice_runs"], 0)
            self.assertEqual(compact["status"], "FAIL")
            summary = json.loads((output / "validation_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(any("real device model required" in item for item in summary["failures"]))

    def test_simulation_failure_fails_fast_but_metric_evaluation_continues(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            net = root / "circuit.net"
            spec = root / "circuit.json"
            net.write_text("V1 in 0 1\n.tran 0 1m\n.ac dec 10 10 10k\n.end\n", encoding="utf-8")
            spec.write_text(json.dumps({"analyses": [
                {"name": "tran", "kind": "tran"}, {"name": "ac", "kind": "ac"},
            ]}), encoding="utf-8")
            failed = {
                "ok": False, "returncode": 1, "fresh_raw": False, "fresh_log": False,
                "errors": ["simulation failed"], "elapsed_seconds": 0.01,
            }
            with patch.object(suite, "run_simulation", return_value=failed) as runner:
                with patch.object(sys, "argv", ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(root / "output")]):
                    self.assertEqual(suite.main(), 1)
                self.assertEqual(runner.call_count, 1)
            summary = json.loads((root / "output" / "validation_summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["simulation_fail_fast_triggered"])
            self.assertEqual(len(summary["analyses"]), 1)
            self.assertIn("ac", summary["skipped_jobs"])

        with patch.object(suite, "raw_arrays", return_value=(np.array([0.0, 1.0]), {"V(out)": np.array([1.0, 2.0])})):
            results, failures = evaluate_metrics(Path("unused.raw"), {
                "good": {"trace": "V(out)", "kind": "value"},
                "bad": {"trace": "V(out)", "kind": "not-a-kind"},
            })
        self.assertIn("good", results)
        self.assertIn("bad", results)
        self.assertEqual(failures, ["bad"])

    def test_convergence_retry_has_distinct_evidence_identity_and_summary_fields(self) -> None:
        with TemporaryDirectory() as folder:
            root = Path(folder)
            net = root / "circuit.net"
            spec = root / "circuit.json"
            net.write_text("V1 in 0 1\n.tran 0 1m\n.end\n", encoding="utf-8")
            spec.write_text(json.dumps({
                "analyses": [{"name": "tran", "kind": "tran"}],
                "convergence_hints": [".options gmin=1e-12"],
            }), encoding="utf-8")
            timeout = {
                "ok": False, "returncode": -1, "fresh_raw": False, "fresh_log": False,
                "timed_out": True, "errors": ["timed out"], "elapsed_seconds": 0.01,
            }
            success = {
                "ok": True, "returncode": 0, "fresh_raw": True, "fresh_log": True,
                "timed_out": False, "errors": [], "elapsed_seconds": 0.01,
            }
            with patch.object(suite, "run_simulation", side_effect=[timeout, success]) as runner:
                with patch.object(sys, "argv", ["run_validation_suite.py", "--net", str(net), "--spec", str(spec), "--ltspice", str(root / "LTspice.exe"), "--output", str(root / "output")]):
                    self.assertEqual(suite.main(), 0)
                self.assertEqual(runner.call_count, 2)
            summary = json.loads((root / "output" / "validation_summary.json").read_text(encoding="utf-8"))
            run = summary["analyses"][0]["run"]
            self.assertEqual(summary["convergence_retries"], 1)
            self.assertTrue(run["convergence_hints_used"])
            self.assertTrue(run["convergence_retry_attempted"])
            self.assertNotEqual(run["base_evidence_key"], run["evidence_key"])
            self.assertEqual(run["convergence_retry_evidence_key"], run["evidence_key"])


if __name__ == "__main__":
    unittest.main()
