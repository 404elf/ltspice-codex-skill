from __future__ import annotations

import contextlib
import io
import json
import math
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import preflight
import run_validation_suite as suite


class TransientSemanticsTests(unittest.TestCase):
    def test_time_averages_are_invariant_to_adaptive_sampling(self):
        for times in ([0, .1, .9, 1], [0, .01, .05, .1, .9, .95, .99, 1]):
            axis = np.asarray(times)
            samples = np.interp(axis, [0, .1, .9, 1], [0, 1, 1, 0])
            for kind, expected in [("mean", .9), ("rms", math.sqrt(13 / 15))]:
                with self.subTest(times=times, kind=kind):
                    measured = suite.metric_value({"kind": kind, "trace": "V(out)"}, axis, {"V(out)": samples}, analysis_kind="tran")
                    self.assertAlmostEqual(measured, expected, places=12)

    def test_rms_integrates_a_linear_ramp_exactly(self):
        actual = suite.metric_value({"kind": "rms", "trace": "V(out)"}, np.array([0., 1.]), {"V(out)": np.array([0., 3.])}, analysis_kind="tran")
        self.assertAlmostEqual(actual, math.sqrt(3))

    def test_nontransient_statistics_keep_sample_semantics(self):
        for kind, expected in [("mean", 1.5), ("rms", math.sqrt(4.5))]:
            actual = suite.metric_value({"kind": kind, "trace": "V(out)"}, np.array([0., 1.]), {"V(out)": np.array([0., 3.])}, analysis_kind="dc")
            self.assertAlmostEqual(actual, expected)

    def test_transient_statistics_reject_invalid_time_coverage(self):
        for axis in ([0.], [0., 0.], [1., 0.], [0., float("nan")]):
            for kind in ("mean", "rms"):
                with self.subTest(axis=axis, kind=kind), self.assertRaises(ValueError):
                    suite.metric_value({"kind": kind, "trace": "V(out)"}, np.asarray(axis), {"V(out)": np.ones(len(axis))}, analysis_kind="tran")

    def test_time_weighting_changes_the_gate_not_just_the_report(self):
        with patch.object(suite, "raw_arrays", return_value=(np.array([0., .1, .9, 1.]), {"V(out)": np.array([0., 1., 1., 0.])})):
            results, failures = suite.evaluate_metrics(Path("unused.raw"), {"correct": {"kind": "mean", "trace": "V(out)", "min": .89, "max": .91}, "sample_bias": {"kind": "mean", "trace": "V(out)", "max": .6}}, analysis_kind="tran")
        self.assertTrue(results["correct"]["ok"])
        self.assertEqual(failures, ["sample_bias"])

    def test_invalid_saved_interval_fails_before_simulation(self):
        source = "* time test\nV1 in 0 1\nR1 in 0 1k\n.tran 0 25m\n.end\n"
        for directive in (".tran 0 5m 20m 100n", ".tran 0 20m 20m", ".tran 0"):
            with self.subTest(directive=directive):
                result = suite.dry_run_spec(source, Path("time.net"), {
                    "analyses": [{"name": "steady", "kind": "tran", "directive": directive}],
                })
                self.assertFalse(result["ok"])
                self.assertTrue(any(".tran" in error for error in result["errors"]))

    def test_valid_short_and_parameterized_transient_forms_are_preserved(self):
        for directive in (".tran 0 25m 20m 100n", ".tran 25m startup", ".tran 0 {stop} {start} uic"):
            with self.subTest(directive=directive):
                self.assertIsNone(suite._validate_tran_directive(directive))

    @staticmethod
    def offset_raw_text(offset="0.2", *, dc=False):
        plot = "DC transfer characteristic" if dc else "Transient Analysis"
        axis = "V1\tvoltage" if dc else "time\ttime"
        return (f"Title: Offset regression\nDate: test\nPlotname: {plot}\nFlags: real forward\n"
                f"No. Variables: 2\nNo. Points: 3\nOffset: {offset}\n"
                f"Command: LTspice\nVariables:\n\t0\t{axis}\n\t1\tV(out)\tvoltage\n"
                "Values:\n0\t0\n\t2\n1\t0.05\n\t3\n2\t0.1\n\t4\n")

    def test_saved_time_offset_is_applied_to_point_measurements(self):
        with TemporaryDirectory() as folder:
            raw = Path(folder) / "offset.raw"
            raw.write_text(self.offset_raw_text(), encoding="utf-8")
            x, values = suite.raw_arrays(raw, ["V(out)"])
            np.testing.assert_allclose(x, [.2, .25, .3])
            measured, failures = suite.evaluate_metrics(raw, {
                "at_250ms": {"kind": "value_at", "trace": "V(out)", "x": .25, "min": 2.99, "max": 3.01},
                "outside": {"kind": "value_at", "trace": "V(out)", "x": .1},
                "mean": {"kind": "mean", "trace": "V(out)", "min": 2.99, "max": 3.01},
            }, analysis_kind="tran")
            self.assertTrue(measured["at_250ms"]["ok"])
            self.assertTrue(measured["mean"]["ok"])
            self.assertEqual(failures, ["outside"])

    def test_raw_inspection_reports_the_same_absolute_time(self):
        import parse_raw
        with TemporaryDirectory() as folder:
            raw = Path(folder) / "offset.raw"
            raw.write_text(self.offset_raw_text(), encoding="utf-8")
            output = io.StringIO()
            with patch.object(sys, "argv", ["parse_raw", "--raw", str(raw), "--trace", "time"]), contextlib.redirect_stdout(output):
                self.assertEqual(parse_raw.main(), 0)
            stats = json.loads(output.getvalue())["stats"]["time"]
            self.assertAlmostEqual(stats["first"], .2)
            self.assertAlmostEqual(stats["last"], .3)

    def test_offset_does_not_shift_dc_axes_and_zero_offset_stays_zero(self):
        with TemporaryDirectory() as folder:
            for dc, offset in ((True, ".2"), (False, "0")):
                raw = Path(folder) / f"offset-{dc}.raw"
                raw.write_text(self.offset_raw_text(offset, dc=dc), encoding="utf-8")
                x, _ = suite.raw_arrays(raw, ["V(out)"])
                np.testing.assert_allclose(x, [0, .05, .1])

    def test_invalid_raw_time_offset_fails(self):
        with TemporaryDirectory() as folder:
            for offset in ("nan", "-0.2"):
                raw = Path(folder) / "bad.raw"
                raw.write_text(self.offset_raw_text(offset), encoding="utf-8")
                with self.assertRaises(ValueError):
                    suite.raw_arrays(raw, ["V(out)"])

    def test_preflight_counts_switch_pins_and_present_ground(self):
        circuit = "* switch\nV1 in 0 5\nVctrl ctrl 0 1\nS1 in out ctrl 0 SWMOD\nR1 out 0 1k\n.model SWMOD SW(Ron=0.01 Roff=1G Vt=.5)\n.tran 0 1m\n.end\n"
        with TemporaryDirectory() as folder:
            net = Path(folder) / "switch.net"
            report = Path(folder) / "report.json"
            net.write_text(circuit, encoding="utf-8")
            argv = ["preflight", "--net", str(net), "--required-net", "0", "--required-net", "ctrl", "--json", str(report)]
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.main(), 0)
            net.write_text(circuit.replace(".end", "Rbad lonely 0 1k\n.end"), encoding="utf-8")
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.main(), 1)
            checks = json.loads(report.read_text(encoding="utf-8"))["checks"]
            self.assertEqual(next(c for c in checks if c["name"] == "no_obvious_single_use_nets")["details"], ["lonely"])

    def test_required_ground_is_not_invented(self):
        with TemporaryDirectory() as folder:
            net = Path(folder) / "floating.net"
            net.write_text("* floating\nV1 a b 1\nR1 a b 1k\n.op\n.end\n", encoding="utf-8")
            with patch.object(sys, "argv", ["preflight", "--net", str(net), "--required-net", "0"]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.main(), 1)


if __name__ == "__main__":
    unittest.main()
