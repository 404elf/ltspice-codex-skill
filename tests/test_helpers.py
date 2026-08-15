from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


from preflight import terminals  # noqa: E402
from validate_log import find_errors  # noqa: E402
from weave_convert import sha256  # noqa: E402


class HelperUnitTests(unittest.TestCase):
    def test_validate_log_ignores_successful_gmin_recovery(self) -> None:
        text = (
            "Direct Newton iteration failed to find the operating point\n"
            "Gmin stepping succeeded in finding the operating point\n"
        )
        self.assertEqual(find_errors(text), [])

    def test_validate_log_rejects_unresolved_error(self) -> None:
        self.assertTrue(find_errors("Error: Unknown device\n"))

    def test_sha256_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sample = Path(folder) / "sample.net"
            sample.write_text("R1 in 0 1k\n", encoding="utf-8")
            self.assertEqual(len(sha256(sample)), 64)

    def test_preflight_reads_mos_bjt_and_jfet_nodes(self) -> None:
        self.assertEqual(terminals("M1 drain gate source bulk NMOS".split()), ["drain", "gate", "source", "bulk"])
        self.assertEqual(terminals("Q1 collector base emitter model".split()), ["collector", "base", "emitter"])
        self.assertEqual(terminals("Q1 collector base emitter substrate model area=1".split()), ["collector", "base", "emitter", "substrate"])
        self.assertEqual(terminals("J1 drain gate source JMOD".split()), ["drain", "gate", "source"])


if __name__ == "__main__":
    unittest.main()
