#!/usr/bin/env python3
"""Run LTspice with fresh-output and log validation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from validate_log import find_errors


def archive(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    target = path.with_name(f"{path.stem}.stale-{stamp}{path.suffix}")
    shutil.move(str(path), str(target))
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and strictly validate an LTspice input.")
    parser.add_argument("--input", required=True, type=Path, help=".net, .cir, or .asc input")
    parser.add_argument("--ltspice", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    args = parser.parse_args()

    input_path = args.input.resolve()
    executable = args.ltspice.resolve()
    if not input_path.is_file():
        print(f"ERROR: input missing: {input_path}", file=sys.stderr)
        return 2
    if not executable.is_file():
        print(f"ERROR: LTspice executable missing: {executable}", file=sys.stderr)
        return 2

    output_dir = input_path.parent
    raw_path = output_dir / f"{input_path.stem}.raw"
    log_path = output_dir / f"{input_path.stem}.log"
    report_path = (args.report.resolve() if args.report else
                   output_dir / f"{input_path.stem}.run-report.json")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stale_raw = archive(raw_path, stamp)
    stale_log = archive(log_path, stamp)
    started_ns = time.time_ns()
    command = [str(executable), "-b", "-Run", "-ascii", str(input_path)]
    completed = subprocess.run(
        command,
        cwd=str(output_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    fresh_raw = raw_path.is_file() and raw_path.stat().st_mtime_ns >= started_ns
    fresh_log = log_path.is_file() and log_path.stat().st_mtime_ns >= started_ns
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if fresh_log else ""
    errors = find_errors(log_text) if fresh_log else ["fresh log file missing"]
    if not fresh_raw:
        errors.insert(0, "fresh raw file missing")
    ok = completed.returncode == 0 and fresh_raw and fresh_log and not errors
    result = {
        "ok": ok,
        "input": str(input_path),
        "command": command,
        "returncode": completed.returncode,
        "fresh_raw": fresh_raw,
        "fresh_log": fresh_log,
        "raw": str(raw_path),
        "log": str(log_path),
        "errors": errors,
        "stale_raw": str(stale_raw) if stale_raw else None,
        "stale_log": str(stale_log) if stale_log else None,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
