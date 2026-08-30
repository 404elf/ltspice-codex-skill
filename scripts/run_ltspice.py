#!/usr/bin/env python3
"""Run LTspice with fresh-output and log validation."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from validate_log import find_errors
from validation_support import dependency_manifest, parse_dependency_line, rewrite_dependency_text


ASC_DEPENDENCY_RE = re.compile(
    r"(?P<prefix>!\s*)(?P<directive>\.(?:include|lib)\s+.+)$",
    re.IGNORECASE,
)


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


def build_netlist_command(executable: Path, input_path: Path) -> list[str]:
    """Ask LTspice itself to parse an ASC before its batch simulation."""

    return [str(executable), "-netlist", str(input_path)]


def stage_asc_with_dependencies(input_path: Path, stage_dir: Path) -> Path:
    """Stage an ASC and its relative model files without changing the source."""

    input_path = input_path.resolve()
    stage_dir.mkdir(parents=True, exist_ok=True)
    text = input_path.read_text(encoding="utf-8", errors="replace")
    directives: list[str] = []
    for line in text.splitlines():
        match = ASC_DEPENDENCY_RE.search(line)
        if match and parse_dependency_line(match.group("directive").strip()):
            directives.append(match.group("directive").strip())
    manifest = dependency_manifest(input_path, "\n".join(directives) + ("\n" if directives else ""))
    staged: dict[str, Path] = {}
    used_targets: set[str] = set()
    source_root = input_path.parent
    unique_sources: list[Path] = []
    for item in manifest.get("files", []):
        if not item.get("content_verified") or not item.get("exists"):
            continue
        source = Path(str(item["resolved"])).resolve()
        key = str(source).casefold()
        if key in staged:
            continue
        try:
            relative = source.relative_to(source_root)
            target = stage_dir / relative
        except ValueError:
            target = stage_dir / "deps" / f"{len(unique_sources) + 1:03d}_{source.name}"
        if str(target).casefold() in used_targets:
            target = stage_dir / "deps" / f"{len(unique_sources) + 1:03d}_{source.name}"
        staged[key] = target
        used_targets.add(str(target).casefold())
        unique_sources.append(source)
    for source in unique_sources:
        target = staged[str(source).casefold()]
        target.parent.mkdir(parents=True, exist_ok=True)
        child = source.read_text(encoding="utf-8", errors="replace")
        target.write_text(rewrite_dependency_text(child, source.parent, target.parent, staged), encoding="utf-8")

    rewritten_lines: list[str] = []
    for line in text.splitlines():
        match = ASC_DEPENDENCY_RE.search(line)
        if not match:
            rewritten_lines.append(line)
            continue
        directive = match.group("directive").strip()
        if not parse_dependency_line(directive):
            rewritten_lines.append(line)
            continue
        rewritten = rewrite_dependency_text(directive, source_root, stage_dir, staged).strip()
        rewritten_lines.append(line[:match.start("directive")] + rewritten + line[match.end("directive"):])
    staged_input = stage_dir / input_path.name
    staged_input.write_text("\n".join(rewritten_lines) + "\n", encoding="utf-8")
    return staged_input


def run_simulation(
    input_path: Path,
    executable: Path,
    report_path: Path | None = None,
    *,
    artifact_stem: str | None = None,
    output_dir: Path | None = None,
    ascii_output: bool = False,
    timeout_seconds: float | None = None,
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

    artifact_dir = output_dir.resolve() if output_dir is not None else input_path.parent
    artifact_dir.mkdir(parents=True, exist_ok=True)
    # Stage ASC validation in a temporary directory. LTspice first netlists the
    # staged ASC, then runs that generated NET, so the source NET is untouched.
    # Its RAW/LOG are copied back under a distinct stem.
    is_asc = input_path.suffix.lower() == ".asc"
    stem = artifact_stem or (f"{input_path.stem}-asc" if is_asc else input_path.stem)
    raw_path = artifact_dir / f"{stem}.raw"
    log_path = artifact_dir / f"{stem}.log"
    report_path = (report_path.resolve() if report_path else
                   artifact_dir / f"{stem}.run-report.json")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stale_raw = archive(raw_path, stamp)
    stale_log = archive(log_path, stamp)
    started_ns = time.time_ns()
    started_perf_ns = time.perf_counter_ns()
    stage_dir: Path | None = None
    run_input = input_path
    run_dir = input_path.parent
    source_raw_path = input_path.with_suffix(".raw")
    source_log_path = input_path.with_suffix(".log")
    pre_run_errors: list[str] = []
    netlist_command: list[str] | None = None
    netlist_returncode: int | None = None
    netlist_stdout = ""
    netlist_stderr = ""
    if is_asc:
        stage_dir = Path(tempfile.mkdtemp(prefix=f".{input_path.stem}-ltspice-", dir=str(input_path.parent)))
        staged_asc = stage_asc_with_dependencies(input_path, stage_dir)
        netlist_command = build_netlist_command(executable, staged_asc)
        try:
            netlisted = subprocess.run(
                netlist_command,
                cwd=str(stage_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_seconds,
            )
            netlist_returncode = netlisted.returncode
            netlist_stdout = netlisted.stdout
            netlist_stderr = netlisted.stderr
            run_input = staged_asc.with_suffix(".net")
            if netlisted.returncode != 0 or not run_input.is_file():
                pre_run_errors.append("LTspice ASC netlist generation failed")
        except subprocess.TimeoutExpired:
            pre_run_errors.append("LTspice ASC netlist generation timed out")
            netlist_returncode = -1
        source_raw_path = stage_dir / f"{run_input.stem}.raw"
        source_log_path = stage_dir / f"{run_input.stem}.log"
        run_dir = stage_dir
    command = build_command(executable, run_input, ascii_output=ascii_output)
    timed_out = False
    try:
        if pre_run_errors:
            completed = subprocess.CompletedProcess(command, -1, stdout="", stderr="")
        else:
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(run_dir),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                completed = subprocess.CompletedProcess(
                    command,
                    -1,
                    stdout=stdout,
                    stderr=stderr,
                )
            except OSError as exc:
                completed = subprocess.CompletedProcess(command, -1, stdout="", stderr=str(exc))
                pre_run_errors.append(str(exc))
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
    errors = list(pre_run_errors)
    errors.extend(find_errors(log_text) if fresh_log else ["fresh log file missing"])
    if not fresh_raw:
        errors.insert(0, "fresh raw file missing")
    if timed_out:
        errors.insert(0, f"simulation timed out after {timeout_seconds:g} seconds")
    ok = completed.returncode == 0 and fresh_raw and fresh_log and not errors
    result = {
        "ok": ok,
        "input": str(input_path),
        "run_input": str(run_input),
        "command": command,
        "returncode": completed.returncode,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
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
        "asc_netlist_command": netlist_command,
        "asc_netlist_returncode": netlist_returncode,
        "asc_netlist_stdout": netlist_stdout,
        "asc_netlist_stderr": netlist_stderr,
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
    parser.add_argument("--output-dir", type=Path, help="Optional RAW/LOG/report output directory")
    parser.add_argument("--ascii", action="store_true", help="Request ASCII RAW output instead of the default binary RAW")
    parser.add_argument("--timeout-seconds", type=float, help="Stop a long-running LTspice job after this many seconds")
    args = parser.parse_args()

    result = run_simulation(
        args.input, args.ltspice, args.report,
        artifact_stem=args.artifact_stem, output_dir=args.output_dir, ascii_output=args.ascii,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
