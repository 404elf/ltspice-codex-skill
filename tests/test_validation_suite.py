from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_validation_suite import (  # noqa: E402
    check_metric,
    expand_corners,
    render_analysis_net,
    replace_parameters,
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

    def test_metric_bounds_are_enforced(self) -> None:
        self.assertEqual(check_metric(5.0, {"min": 4, "max": 6}), (True, None))
        ok, reason = check_metric(7.0, {"max": 6})
        self.assertFalse(ok)
        self.assertIn("> max", reason or "")


if __name__ == "__main__":
    unittest.main()
