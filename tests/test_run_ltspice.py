from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from run_ltspice import build_command  # noqa: E402


class LTspiceCommandTests(unittest.TestCase):
    def test_binary_raw_is_the_default(self) -> None:
        command = build_command(Path("LTspice.exe"), Path("circuit.net"))
        self.assertNotIn("-ascii", command)

    def test_ascii_raw_is_opt_in(self) -> None:
        command = build_command(Path("LTspice.exe"), Path("circuit.net"), ascii_output=True)
        self.assertIn("-ascii", command)


if __name__ == "__main__":
    unittest.main()
