#!/usr/bin/env python3
"""Run LTspice with fresh-output and log validation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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


def build_command(executable: Path, input_path: Path, *, ascii_output: bool = False) -> list[str]:
    command = [str(executable), "-b", "-Run"]
    if ascii_output:
        command.append("-ascii")
    command.append(str(input_path))
    return command


def run_simulation(
    input_path: Path,
    executable: Path,
    report_path: Path | None = None,
    *,
    artifact_stem: str | None = None,
    ascii_output: bool = False,
) -> dict[str, object]:
    """Run one LTspice job and return its deterministic validation record.

    LTspice writes binary RAW files by default.  ASCII RAW output remains
    available for diagnostics through ``ascii_output=True``.
    """

    input_path = input_path.resolve()
    executable = executable.resolve()
    if not input_path.is_file():
        return {"ok": False, "input": str(input_path), "errors": ["input missing"]}
    if not executable.is_file():
        return {"ok": False, "input": str(input_path), "errors": ["LTspice executable missing"]}

    output_dir = input_path.parent
    # LTspice writes a same-stem .net while batch-running an .asc.  Stage ASC
    # validation in a temporary directory so that it can never overwrite the
    # exact source NET.  Its RAW/LOG are copied back under a distinct stem.
    is_asc = input_path.suffix.lower() == ".asc"
    stem = artifact_stem or (f"{input_path.stem}-asc" if is_asc else input_path.stem)
    raw_path = output_dir / f"{stem}.raw"
    log_path = output_dir / f"{stem}.log"
    report_path = (report_path.resolve() if report_path else
                   output_dir / f"{stem}.run-report.json")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stale_raw = archive(raw_path, stamp)
    stale_log = archive(log_path, stamp)
    started_ns = time.time_ns()
    started_perf_ns = time.perf_counter_ns()
    stage_dir: Path | None = None
    run_input = input_path
    run_dir = output_dir
    source_raw_path = raw_path
    source_log_path = log_path
    if is_asc:
        stage_dir = Path(tempfile.mkdtemp(prefix=f".{input_path.stem}-ltspice-", dir=str(output_dir)))
        run_input = stage_dir / input_path.name
        shutil.copy2(str(input_path), str(run_input))
        source_raw_path = stage_dir / f"{run_input.stem}.raw"
        source_log_path = stage_dir / f"{run_input.stem}.log"
        run_dir = stage_dir
    command = build_command(executable, run_input, ascii_output=ascii_output)
    try:
        completed = subprocess.run(
            command,
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        source_fresh_raw = source_raw_path.is_file() and source_raw_path.stat().st_mtime_ns >= started_ns
        source_fresh_log = source_log_path.is_file() and source_log_path.stat().st_mtime_ns >= started_ns
        if source_fresh_raw and source_raw_path.resolve() != raw_path.resolve():
            shutil.copyfile(str(source_raw_path), str(raw_path))
        if source_fresh_log and source_log_path.resolve() != log_path.resolve():
            shutil.copyfile(str(source_log_path), str(log_path))
        # copyfile preserves no timestamps; make freshness explicit for the
        # output artifacts used by the validator.
        if source_fresh_raw and raw_path.exists():
            os.utime(raw_path, None)
        if source_fresh_log and log_path.exists():
            os.utime(log_path, None)
    finally:
        if stage_dir is not None:
            shutil.rmtree(stage_dir, ignore_errors=True)

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
        "run_input": str(run_input),
        "command": command,
        "returncode": completed.returncode,
        "fresh_raw": fresh_raw,
        "fresh_log": fresh_log,
        "raw": str(raw_path),
        "log": str(log_path),
        "errors": errors,
        "started_at_utc": datetime.fromtimestamp(started_ns / 1_000_000_000, timezone.utc).isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round((time.perf_counter_ns() - started_perf_ns) / 1_000_000_000, 6),
        "stale_raw": str(stale_raw) if stale_raw else None,
        "stale_log": str(stale_log) if stale_log else None,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if report_path is not None:
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and strictly validate an LTspice input.")
    parser.add_argument("--input", required=True, type=Path, help=".net, .cir, or .asc input")
    parser.add_argument("--ltspice", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    parser.add_argument("--artifact-stem", help="Optional output RAW/LOG stem")
    parser.add_argument("--ascii", action="store_true", help="Request ASCII RAW output instead of the default binary RAW")
    args = parser.parse_args()

    result = run_simulation(
        args.input, args.ltspice, args.report,
        artifact_stem=args.artifact_stem, ascii_output=args.ascii,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
