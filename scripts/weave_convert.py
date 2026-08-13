#!/usr/bin/env python3
"""Convert one exact netlist with Weave and require round-trip MATCH."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Weave-convert and verify an exact netlist.")
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--weave-dir", required=True, type=Path)
    parser.add_argument("--node", default="node")
    parser.add_argument("--asc", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    net = args.net.resolve()
    weave_dir = args.weave_dir.resolve()
    weave_js = weave_dir / "weave.js"
    asc = (args.asc.resolve() if args.asc else net.with_suffix(".asc"))
    result_path = (args.result.resolve() if args.result else
                   net.with_name(f"{net.stem}.weave-verification.txt"))
    if not net.is_file() or not weave_js.is_file():
        print("ERROR: netlist or weave.js missing", file=sys.stderr)
        return 2
    if asc.parent != net.parent or result_path.parent != net.parent:
        print("ERROR: ASC and verification result must be beside the netlist", file=sys.stderr)
        return 2
    if asc.exists() and not args.force:
        print(f"ERROR: refusing to overwrite {asc}; use --force", file=sys.stderr)
        return 2

    before = sha256(net)
    conversion = run([args.node, str(weave_js), "convert", str(net), str(asc)], weave_dir)
    verification = None
    verdict = "ERROR"
    after = sha256(net)
    if conversion.returncode == 0 and asc.is_file() and after == before:
        verification = run([args.node, str(weave_js), "verify", str(net), str(asc)], weave_dir)
        match = verification.returncode == 0 and re.search(
            r"(?im)^\s*MATCH(?:\s|$)", verification.stdout or ""
        )
        verdict = "MATCH" if match else "MISMATCH"
    else:
        verdict = "NET_CHANGED_OR_CONVERSION_FAILED"

    lines = [
        "Weave netlist-to-ASC verification",
        f"INPUT_NET={net}",
        f"OUTPUT_ASC={asc}",
        f"NET_SHA256_BEFORE={before}",
        f"NET_SHA256_AFTER={after}",
        f"VERDICT={verdict}",
        f"CONVERSION_EXIT_CODE={conversion.returncode}",
        "CONVERSION_STDOUT_BEGIN", conversion.stdout.rstrip(), "CONVERSION_STDOUT_END",
        "CONVERSION_STDERR_BEGIN", conversion.stderr.rstrip(), "CONVERSION_STDERR_END",
    ]
    if verification is not None:
        lines.extend([
            f"VERIFICATION_EXIT_CODE={verification.returncode}",
            "VERIFICATION_STDOUT_BEGIN", verification.stdout.rstrip(), "VERIFICATION_STDOUT_END",
            "VERIFICATION_STDERR_BEGIN", verification.stderr.rstrip(), "VERIFICATION_STDERR_END",
        ])
    result_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"VERDICT={verdict}")
    print(f"ASC={asc}")
    print(f"RESULT={result_path}")
    return 0 if verdict == "MATCH" else 1


if __name__ == "__main__":
    raise SystemExit(main())
