#!/usr/bin/env python3
"""Run a deterministic LTspice validation suite from a small JSON spec.

The suite keeps model/agent round-trips out of repeated analysis and corner
execution.  It never treats cached RAW/LOG files as a fresh simulation: every
LTspice job is delegated to run_ltspice.run_simulation(), which archives stale
sidecars and requires a new RAW and LOG.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PyLTSpice import RawRead

from run_ltspice import run_simulation


SUITE_VERSION = "1"
ANALYSIS_RE = re.compile(r"^\s*\.(tran|ac|dc|op|noise|tf|pz)\b", re.IGNORECASE)
PARAM_RE = re.compile(r"(\.param\s+)([A-Za-z_][\w]*)\s*=\s*([^\s;]+)", re.IGNORECASE)
SUFFIXES = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)([a-z]+)?", text, re.IGNORECASE)
        if not match or not match.group(2):
            raise ValueError(f"not a numeric SPICE value: {value}")
        suffix = match.group(2).lower()
        if suffix not in SUFFIXES:
            raise ValueError(f"unsupported SPICE suffix: {suffix}")
        return float(match.group(1)) * SUFFIXES[suffix]


def format_value(value: object) -> str:
    if isinstance(value, str):
        return value
    number = float(value)
    return f"{number:.12g}"


def source_analysis_directives(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = ANALYSIS_RE.match(line)
        if match:
            found.append((match.group(1).lower(), line.strip()))
    return found


def normalize_analysis(item: object) -> dict[str, object]:
    if isinstance(item, str):
        return {"name": item, "kind": item.lower()}
    if not isinstance(item, dict):
        raise ValueError("each analysis must be a string or object")
    name = str(item.get("name") or item.get("kind") or "analysis")
    kind = str(item.get("kind") or name).lower()
    result = dict(item)
    result.update({"name": name, "kind": kind})
    return result


def replace_parameters(text: str, params: dict[str, object]) -> str:
    if not params:
        return text
    remaining = {str(key).lower(): (str(key), value) for key, value in params.items()}

    def replace_match(match: re.Match[str]) -> str:
        key = match.group(2).lower()
        if key not in remaining:
            return match.group(0)
        _, value = remaining.pop(key)
        return f"{match.group(1)}{match.group(2)}={format_value(value)}"

    lines = [PARAM_RE.sub(replace_match, line) for line in text.splitlines()]
    insert_at = next((index for index, line in enumerate(lines) if line.strip().lower() == ".end"), len(lines))
    additions = [f".param {key}={format_value(value)}" for key, value in remaining.values()]
    lines[insert_at:insert_at] = additions
    return "\n".join(lines) + "\n"


def render_analysis_net(text: str, analysis: dict[str, object], params: dict[str, object]) -> str:
    kind = str(analysis["kind"]).lower()
    directive = str(analysis.get("directive") or "").strip()
    source_directives = source_analysis_directives(text)
    if not directive:
        matches = [line for name, line in source_directives if name == kind]
        if matches:
            directive = matches[0]
    if not directive:
        raise ValueError(f"analysis {kind!r} needs a directive in the spec or source NET")
    lines = [line for line in text.splitlines() if not ANALYSIS_RE.match(line)]
    insert_at = next((index for index, line in enumerate(lines) if line.strip().lower() == ".end"), len(lines))
    lines.insert(insert_at, directive)
    return replace_parameters("\n".join(lines) + "\n", params)


def expand_corners(source_text: str, raw_corners: object) -> list[dict[str, object]]:
    if not raw_corners:
        return []
    if isinstance(raw_corners, list):
        result = []
        for index, item in enumerate(raw_corners):
            if not isinstance(item, dict):
                raise ValueError("corner list entries must be objects")
            result.append({"name": str(item.get("name", f"corner-{index + 1}")),
                           "params": dict(item.get("params", {})),
                           "analysis": item.get("analysis")})
        return result
    if not isinstance(raw_corners, dict):
        raise ValueError("corners must be a list or an object")

    base_values: dict[str, float] = {}
    for match in PARAM_RE.finditer(source_text):
        try:
            base_values[match.group(2).lower()] = parse_number(match.group(3))
        except ValueError:
            continue
    items = list(raw_corners.items())
    choices: list[list[tuple[str, object]]] = []
    for key, bounds in items:
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError(f"corner bounds for {key} must be [low_percent, high_percent]")
        if str(key).lower() not in base_values:
            raise ValueError(f"corner parameter {key} has no numeric .param base value")
        base = base_values[str(key).lower()]
        choices.append([(f"{key}={bounds[0]}%", base * (1 + float(bounds[0]) / 100.0)),
                        (f"{key}={bounds[1]}%", base * (1 + float(bounds[1]) / 100.0))])
    result = []
    for index, combination in enumerate(itertools.product(*choices)):
        params = {str(label).split("=", 1)[0]: value for label, value in combination}
        name = "corner-" + "-".join(str(label).replace("=", "_").replace("%", "pct") for label, _ in combination)
        result.append({"name": name[:96], "params": params, "analysis": None})
    return result


def trace_name(names: list[str], requested: str) -> str:
    for name in names:
        if name.lower() == requested.lower():
            return name
    raise KeyError(f"trace not found: {requested}; available={names}")


def raw_arrays(raw_path: Path, traces: list[str]) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    raw = RawRead(raw_path, traces_to_read=traces or None, verbose=False)
    names = list(raw.get_trace_names())
    resolved = {item: trace_name(names, item) for item in traces}
    values = {requested: np.asarray(raw.get_trace(actual).get_wave()) for requested, actual in resolved.items()}
    if "frequency" in (name.lower() for name in names):
        axis_name = trace_name(names, "frequency")
        axis = np.asarray(raw.get_trace(axis_name).get_wave())
    else:
        axis = np.asarray(raw.get_axis())
    return axis, values


def metric_value(spec: dict[str, object], axis: np.ndarray, values: dict[str, np.ndarray]) -> float:
    kind = str(spec.get("kind", "abs_max")).lower()
    trace = str(spec.get("trace", ""))
    data = np.asarray(values[trace]).reshape(-1)
    real = np.real(data)
    if kind in {"min", "minimum"}:
        return float(np.min(real))
    if kind in {"max", "maximum"}:
        return float(np.max(real))
    if kind in {"abs_max", "peak"}:
        return float(np.max(np.abs(data)))
    if kind in {"peak_to_peak", "p2p"}:
        return float(np.max(real) - np.min(real))
    if kind == "mean":
        return float(np.mean(real))
    if kind == "rms":
        return float(np.sqrt(np.mean(np.square(real))))
    if kind in {"final", "last"}:
        return float(real[-1])
    if kind in {"value_at", "value_at_x"}:
        target = float(spec["x"])
        index = int(np.argmin(np.abs(np.real(axis.reshape(-1)) - target)))
        return float(np.real(data[index]))
    if kind in {"gain_at", "gain_at_frequency"}:
        reference = str(spec["reference"])
        reference_data = np.asarray(values[reference]).reshape(-1)
        target = float(spec["x"])
        index = int(np.argmin(np.abs(np.real(axis.reshape(-1)) - target)))
        denominator = abs(reference_data[index])
        if denominator == 0:
            raise ValueError("gain reference is zero at requested x")
        return float(abs(data[index]) / denominator)
    if kind in {"fc_3db", "cutoff_3db"}:
        reference = spec.get("reference")
        response = np.abs(data)
        if reference:
            reference_data = np.asarray(values[str(reference)]).reshape(-1)
            response = response / np.maximum(np.abs(reference_data), np.finfo(float).tiny)
        x = np.real(axis.reshape(-1))
        if response.size < 2:
            raise ValueError("not enough samples for fc_3db")
        threshold = float(np.max(response)) / math.sqrt(2.0)
        direction = str(spec.get("response", "lowpass")).lower()
        if direction == "highpass":
            indices = np.where(response <= threshold)[0]
            indices = indices[indices < int(np.argmax(response))]
            index = int(indices[-1]) if indices.size else 0
        else:
            peak = int(np.argmax(response))
            indices = np.where(response[peak:] <= threshold)[0]
            index = peak + int(indices[0]) if indices.size else len(response) - 1
        return float(x[index])
    raise ValueError(f"unsupported metric kind: {kind}")


def metric_specs_for_job(specs: object, job_name: str, job_kind: str) -> dict[str, dict[str, object]]:
    if not isinstance(specs, dict):
        return {}
    selected: dict[str, dict[str, object]] = {}
    for name, raw in specs.items():
        if not isinstance(raw, dict):
            continue
        target = raw.get("analysis")
        target_name = str(target).lower() if target is not None else None
        job_lower = job_name.lower()
        if target_name is None or target_name in {job_lower, job_kind.lower()} or job_lower.endswith("__" + target_name):
            selected[str(name)] = dict(raw)
    return selected


def check_metric(value: float, spec: dict[str, object]) -> tuple[bool, str | None]:
    if "min" in spec and value < float(spec["min"]):
        return False, f"value {value} < min {spec['min']}"
    if "max" in spec and value > float(spec["max"]):
        return False, f"value {value} > max {spec['max']}"
    if "target" in spec:
        target = float(spec["target"])
        tolerance = float(spec.get("tolerance_percent", 0.0)) / 100.0
        if abs(target) > np.finfo(float).tiny:
            lower, upper = target * (1 - tolerance), target * (1 + tolerance)
        else:
            lower, upper = -tolerance, tolerance
        if not lower <= value <= upper:
            return False, f"value {value} outside target band [{lower}, {upper}]"
    return True, None


def evaluate_metrics(raw_path: Path, specs: dict[str, dict[str, object]]) -> tuple[dict[str, object], list[str]]:
    traces = []
    for spec in specs.values():
        for key in ("trace", "reference"):
            if spec.get(key) and str(spec[key]) not in traces:
                traces.append(str(spec[key]))
    if not specs:
        return {}, []
    axis, values = raw_arrays(raw_path, traces)
    results: dict[str, object] = {}
    failures: list[str] = []
    for name, spec in specs.items():
        try:
            value = metric_value(spec, axis, values)
            ok, reason = check_metric(value, spec)
            results[name] = {"value": value, "ok": ok, "reason": reason, "spec": spec}
            if not ok:
                failures.append(name)
        except Exception as exc:  # report as a failed metric, not a Python traceback
            results[name] = {"value": None, "ok": False, "reason": str(exc), "spec": spec}
            failures.append(name)
    return results, failures


def run_preflight(net: Path, output: Path, required_nets: list[str], reuse: dict[str, object] | None) -> tuple[dict[str, object], float]:
    if reuse is not None:
        return reuse, 0.0
    command = [sys.executable, str(Path(__file__).with_name("preflight.py")), "--net", str(net), "--json", str(output)]
    for item in required_nets:
        command.extend(["--required-net", item])
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    elapsed = time.perf_counter() - started
    if output.is_file():
        result = json.loads(output.read_text(encoding="utf-8"))
    else:
        result = {"ok": False, "errors": [completed.stderr or completed.stdout or "preflight failed"]}
    result["exit_code"] = completed.returncode
    result["elapsed_seconds"] = round(elapsed, 6)
    return result, elapsed


def copy_artifact(source: Path, destination: Path) -> str | None:
    if not source.is_file():
        return None
    if source.resolve() == destination.resolve():
        return str(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return str(destination.resolve())


def render_markdown(summary: dict[str, object], path: Path) -> None:
    timing = summary.get("timing", {})
    lines = [
        "# LTspice validation summary",
        "",
        f"- Status: **{'PASS' if summary.get('ok') else 'FAIL'}**",
        f"- NET: `{summary.get('net')}`",
        f"- LTspice runs: `{summary.get('ltspice_runs')}`",
        f"- Deterministic tool time: `{timing.get('deterministic_tool_total_seconds', 0)} s`",
        "",
        "## Metrics",
    ]
    metrics = summary.get("metrics", {})
    if isinstance(metrics, dict) and metrics:
        for name, item in metrics.items():
            if isinstance(item, dict):
                lines.append(f"- `{name}`: `{item.get('value')}` — {'PASS' if item.get('ok') else 'FAIL'}")
    else:
        lines.append("- No metric assertions requested.")
    lines.extend(["", "## Artifacts"])
    artifacts = summary.get("artifact_paths", {})
    if isinstance(artifacts, dict):
        for name, value in artifacts.items():
            if value:
                lines.append(f"- {name}: `{value}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic LTspice analyses, corners, metrics, and summary generation.")
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path, help="Small JSON validation specification")
    parser.add_argument("--ltspice", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Circuit output folder; defaults to the NET folder")
    parser.add_argument("--summary", type=Path, help="JSON summary path; defaults to output/validation_summary.json")
    parser.add_argument("--markdown", type=Path, help="Optional human-readable summary path")
    args = parser.parse_args()

    net = args.net.resolve()
    spec_path = args.spec.resolve()
    output = (args.output or net.parent).resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary_path = (args.summary or output / "validation_summary.json").resolve()
    markdown_path = args.markdown.resolve() if args.markdown else None
    started_wall = datetime.now(timezone.utc).isoformat()
    started_perf = time.perf_counter()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "suite_version": SUITE_VERSION,
        "ok": False,
        "net": str(net),
        "spec": str(spec_path),
        "net_sha256": None,
        "spec_sha256": None,
        "started_at_utc": started_wall,
        "analyses": [],
        "corners": [],
        "metrics": {},
        "failed_corners": [],
        "log_error_status": {},
        "ltspice_runs": 0,
        "model_readable_summary_count": 1,
        "artifact_paths": {"net": str(net), "validation_summary": str(summary_path)},
        "timing": {},
    }

    try:
        if not net.is_file() or not spec_path.is_file():
            raise FileNotFoundError("NET or specification is missing")
        source_text = net.read_text(encoding="utf-8", errors="replace")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("validation specification must be a JSON object")
        summary["net_sha256"] = sha256(net)
        summary["spec_sha256"] = sha256(spec_path)

        preflight = None
        preflight_seconds = 0.0
        if bool(spec.get("preflight", False)):
            state_path = output / ".validation-state.json"
            state_key = json_hash({"net": summary["net_sha256"], "spec": summary["spec_sha256"], "suite": SUITE_VERSION})
            reuse = None
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if state.get("preflight_key") == state_key:
                        reuse = state.get("preflight")
                except (OSError, json.JSONDecodeError):
                    reuse = None
            preflight_path = output / f"{net.stem}.preflight.json"
            preflight, preflight_seconds = run_preflight(net, preflight_path, list(spec.get("required_nets", [])), reuse)
            if reuse is None:
                state_path.write_text(json.dumps({"preflight_key": state_key, "preflight": preflight}, indent=2) + "\n", encoding="utf-8")
            summary["preflight"] = preflight
            summary["artifact_paths"]["preflight"] = str(preflight_path)

        analyses = [normalize_analysis(item) for item in spec.get("analyses", [])]
        if not analyses:
            directives = source_analysis_directives(source_text)
            if directives:
                analyses = [{"name": directives[0][0], "kind": directives[0][0]}]
            else:
                raise ValueError("spec has no analyses and NET has no analysis directive")
        source_kinds = [kind for kind, _ in source_analysis_directives(source_text)]
        exact_kind = source_kinds[0] if len(source_kinds) == 1 else None
        primary = analyses[0]
        if exact_kind and any(str(item["kind"]).lower() == exact_kind for item in analyses):
            primary = next(item for item in analyses if str(item["kind"]).lower() == exact_kind)

        all_failures: list[str] = []
        with tempfile.TemporaryDirectory(prefix=".validation-suite-", dir=str(output)) as work_text:
            work = Path(work_text)

            def execute_job(job_name: str, analysis: dict[str, object], params: dict[str, object], exact: bool, corner: str | None = None) -> dict[str, object]:
                job_start = time.perf_counter()
                kind = str(analysis["kind"]).lower()
                if exact and not params:
                    job_net = net
                    prefix = net.stem
                else:
                    job_net = work / f"{net.stem}__{job_name}.net"
                    job_net.write_text(render_analysis_net(source_text, analysis, params), encoding="utf-8")
                    prefix = f"{net.stem}__{job_name}"
                run_report = work / f"{prefix}.run-report.json"
                result = run_simulation(job_net, args.ltspice, run_report)
                summary["ltspice_runs"] += 1
                raw_source = Path(str(result.get("raw", work / f"{prefix}.raw")))
                log_source = Path(str(result.get("log", work / f"{prefix}.log")))
                artifacts: dict[str, object] = {"input": str(net), "raw": None, "log": None, "run_report": None}
                if exact:
                    artifacts["raw"] = str(raw_source) if raw_source.is_file() else None
                    artifacts["log"] = str(log_source) if log_source.is_file() else None
                    artifacts["run_report"] = copy_artifact(run_report, output / f"{prefix}.run-report.json")
                else:
                    artifacts["raw"] = copy_artifact(raw_source, output / f"{prefix}.raw")
                    artifacts["log"] = copy_artifact(log_source, output / f"{prefix}.log")
                    artifacts["run_report"] = copy_artifact(run_report, output / f"{prefix}.run-report.json")
                log_errors = list(result.get("errors", []))
                summary["log_error_status"][job_name] = {"ok": not log_errors, "errors": log_errors}
                metric_specs = metric_specs_for_job(spec.get("metrics", {}), job_name, kind)
                metric_results: dict[str, object] = {}
                metric_failures: list[str] = []
                readable_raw = raw_source if raw_source.is_file() else None
                if result.get("ok") and readable_raw and metric_specs:
                    try:
                        metric_results, metric_failures = evaluate_metrics(readable_raw, metric_specs)
                    except Exception as exc:
                        metric_failures = list(metric_specs)
                        metric_results = {
                            name: {"value": None, "ok": False, "reason": str(exc), "spec": metric}
                            for name, metric in metric_specs.items()
                        }
                elif metric_specs:
                    metric_failures = list(metric_specs)
                for name, value in metric_results.items():
                    summary["metrics"][f"{job_name}.{name}"] = value
                failures = log_errors + metric_failures
                job = {
                    "name": job_name,
                    "kind": kind,
                    "corner": corner,
                    "ok": bool(result.get("ok")) and not metric_failures,
                    "runtime_seconds": result.get("elapsed_seconds", round(time.perf_counter() - job_start, 6)),
                    "run": {key: result.get(key) for key in ("returncode", "fresh_raw", "fresh_log", "errors", "stale_raw", "stale_log")},
                    "metrics": metric_results,
                    "artifacts": artifacts,
                    "failures": failures,
                }
                return job

            primary_job = execute_job(str(primary["name"]), primary, {}, exact=True)
            summary["analyses"].append(primary_job)
            if not primary_job["ok"]:
                all_failures.extend([f"{primary_job['name']}:{item}" for item in primary_job["failures"]])

            for analysis in analyses:
                if analysis is primary:
                    continue
                job = execute_job(str(analysis["name"]), analysis, {}, exact=False)
                summary["analyses"].append(job)
                if not job["ok"]:
                    all_failures.extend([f"{job['name']}:{item}" for item in job["failures"]])

            corners = expand_corners(source_text, spec.get("corners") or spec.get("sweep"))
            corner_analysis = primary
            corner_start = time.perf_counter()
            for corner in corners:
                chosen = corner.get("analysis")
                analysis = next((item for item in analyses if str(item["name"]).lower() == str(chosen).lower()), corner_analysis) if chosen else corner_analysis
                job_name = f"{corner['name']}__{analysis['name']}"
                job = execute_job(job_name, analysis, dict(corner.get("params", {})), exact=False, corner=str(corner["name"]))
                summary["corners"].append(job)
                if not job["ok"]:
                    summary["failed_corners"].append(str(corner["name"]))
                    all_failures.extend([f"{job_name}:{item}" for item in job["failures"]])
            corner_seconds = time.perf_counter() - corner_start if corners else 0.0

        analysis_times = {str(item["name"]): item.get("runtime_seconds", 0) for item in summary["analyses"]}
        summary["timing"] = {
            "preflight_seconds": round(preflight_seconds, 6),
            "analysis_runtime_seconds": analysis_times,
            "nominal_ltspice_seconds": round(float(summary["analyses"][0].get("runtime_seconds", 0)), 6),
            "corner_sweep_seconds": round(corner_seconds, 6),
            "deterministic_tool_total_seconds": round(time.perf_counter() - started_perf, 6),
        }
        summary["failures"] = all_failures
        summary["ok"] = not all_failures and all(bool(item.get("ok")) for item in summary["analyses"] + summary["corners"])
    except Exception as exc:
        summary["failures"] = [str(exc)]
        summary["ok"] = False
        summary["timing"] = {"deterministic_tool_total_seconds": round(time.perf_counter() - started_perf, 6)}

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary["artifact_paths"]["validation_summary"] = str(summary_path)
    if markdown_path:
        render_markdown(summary, markdown_path)
        summary["artifact_paths"]["verification_summary"] = str(markdown_path)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
