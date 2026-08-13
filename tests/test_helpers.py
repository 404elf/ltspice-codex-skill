from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


from validate_log import find_errors  # noqa: E402
from weave_convert import sha256  # noqa: E402


def test_validate_log_ignores_successful_gmin_recovery():
    text = "Direct Newton iteration failed to find the operating point\nGmin stepping succeeded in finding the operating point\n"
    assert find_errors(text) == []


def test_validate_log_rejects_unresolved_error():
    assert find_errors("Error: Unknown device\n")


def test_sha256_is_stable(tmp_path: Path):
    sample = tmp_path / "sample.net"
    sample.write_text("R1 in 0 1k\n", encoding="utf-8")
    assert len(sha256(sample)) == 64
