#!/usr/bin/env python3
"""Run a deterministic LTspice validation suite from a small JSON spec.

The suite keeps model/agent round-trips out of repeated analysis and corner
execution.  It never treats cached RAW/LOG files as a fresh simulation: every
LTspice job is delegated to run_ltspice.run_simulation(), which archives stale
sidecars and requires a new RAW and LOG.
"""

from __future__ import annotations

import argparse
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
from validation_support import (
    EvidenceStore,
    dependency_manifest,
    format_value as support_format_value,
    json_hash as support_json_hash,
    parameter_values,
    replace_parameters as support_replace_parameters,
    sha256_file,
    simulation_evidence_payload,
    stage_net_with_dependencies,
)


SUITE_VERSION = "5"
PREFLIGHT_VERSION = "2"
ANALYSIS_RE = re.compile(r"^\s*\.(tran|ac|dc|op|noise|tf|pz)\b", re.IGNORECASE)
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
    return sha256_file(path)


def json_hash(value: object) -> str:
    return support_json_hash(value)


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
    return support_format_value(value)


def source_analysis_directives(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = ANALYSIS_RE.match(line)
        if match:
            found.append((match.group(1).lower(), line.strip()))
    return found


def can_use_exact_source(source_directives: list[tuple[str, str]], analysis: dict[str, object]) -> bool:
    """Return whether the original NET is exactly this one requested analysis."""

    if len(source_directives) != 1:
        return False
    source_kind, source_line = source_directives[0]
    if str(analysis.get("kind", "")).lower() != source_kind:
        return False
    requested_line = str(analysis.get("directive") or "").strip()
    return not requested_line or requested_line.casefold() == source_line.casefold()


def preflight_result_ok(result: dict[str, object] | None) -> bool:
    """Treat a missing optional preflight as neutral, but gate enabled checks."""

    if result is None:
        return True
    return bool(result.get("ok")) and int(result.get("exit_code", 0)) == 0


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


ANALYSIS_KINDS = {"tran", "ac", "dc", "op", "noise", "tf", "pz"}
AXIS_METRICS = {
    "value_at", "value_at_x", "gain_at", "gain_at_frequency", "fc_3db", "cutoff_3db",
}
REFERENCE_METRICS = {"gain_at", "gain_at_frequency"}
SCALAR_METRICS = {
    "value", "scalar", "min", "minimum", "max", "maximum", "abs", "absolute", "abs_max",
    "peak", "peak_to_peak", "p2p", "mean", "rms", "final", "last",
}
CONVERGENCE_HINT_PREFIXES = (".options", ".nodeset", ".ic")


def directive_kind(line: str) -> str | None:
    match = re.match(r"^\s*\.(tran|ac|dc|op|noise|tf|pz)\b", line, re.IGNORECASE)
    return match.group(1).lower() if match else None


def inject_validation_hints(text: str, hints: object) -> str:
    if not hints:
        return text
    if not isinstance(hints, list) or not all(isinstance(item, str) for item in hints):
        raise ValueError("convergence_hints must be a list of directive strings")
    lines = text.splitlines()
    insert_at = next((index for index, line in enumerate(lines) if line.strip().lower() == ".end"), len(lines))
    lines[insert_at:insert_at] = [str(item).strip() for item in hints]
    return "\n".join(lines) + "\n"


def resolve_analysis_directive(source_directives: list[tuple[str, str]], analysis: dict[str, object]) -> tuple[str | None, str | None]:
    kind = str(analysis.get("kind", "")).lower()
    explicit = str(analysis.get("directive") or "").strip()
    if explicit:
        actual_kind = directive_kind(explicit)
        if actual_kind != kind:
            return None, f"analysis {analysis.get('name')} directive kind does not match {kind}"
        return explicit, None
    matches = [line for name, line in source_directives if name == kind]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"analysis {analysis.get('name')} has no directive in spec or NET"
    return None, f"analysis {analysis.get('name')} is ambiguous: NET contains {len(matches)} .{kind} directives"


def _validate_dc_directive(directive: str) -> str | None:
    tokens = directive.split()
    if len(tokens) < 5:
        return ".dc requires source, start, stop, and increment"
    try:
        start = parse_number(tokens[2])
        stop = parse_number(tokens[3])
        increment = parse_number(tokens[4])
    except ValueError:
        return ".dc start/stop/increment must be numeric for dry-run validation"
    if start == stop:
        return ".dc start=stop creates no sweep axis; use .op or a real sweep"
    if increment == 0:
        return ".dc increment must not be zero"
    return None


def _metric_analysis_target(raw: dict[str, object], analyses: list[dict[str, object]]) -> dict[str, object] | None:
    target = raw.get("analysis")
    if target is None:
        return analyses[0] if len(analyses) == 1 else None
    target_lower = str(target).lower()
    matches = [item for item in analyses if target_lower in {
        str(item.get("name", "")).lower(), str(item.get("kind", "")).lower(),
    }]
    return matches[0] if len(matches) == 1 else None


def dry_run_spec(source_text: str, net: Path, spec: object) -> dict[str, object]:
    """Validate the spec without invoking LTspice or reading a RAW file."""

    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(spec, dict):
        return {"ok": False, "errors": ["validation specification must be a JSON object"], "warnings": []}

    required_nets = spec.get("required_nets", [])
    if not isinstance(required_nets, list) or not all(isinstance(item, str) and item.strip() for item in required_nets):
        errors.append("required_nets must be a list of non-empty strings")
    if "simulation_fail_fast" in spec and not isinstance(spec.get("simulation_fail_fast"), bool):
        errors.append("simulation_fail_fast must be a boolean")

    params, param_errors = parameter_values(source_text)
    errors.extend(f"parameter parser: {item}" for item in param_errors)
    dependencies = dependency_manifest(net, source_text)
    errors.extend(str(item) for item in dependencies.get("errors", []))
    warnings.extend(str(item) for item in dependencies.get("warnings", []))
    source_directives = source_analysis_directives(source_text)
    raw_analyses = spec.get("analyses", [])
    if raw_analyses is None:
        raw_analyses = []
    if not isinstance(raw_analyses, list):
        errors.append("analyses must be a list")
        raw_analyses = []
    analyses: list[dict[str, object]] = []
    names: set[str] = set()
    for item in raw_analyses:
        try:
            normalized = normalize_analysis(item)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        name = str(normalized["name"])
        kind = str(normalized["kind"]).lower()
        if kind not in ANALYSIS_KINDS:
            errors.append(f"unsupported analysis kind: {kind}")
        if name.lower() in names:
            errors.append(f"duplicate analysis name: {name}")
        names.add(name.lower())
        analyses.append(normalized)
    if not analyses:
        if len(source_directives) == 1:
            analyses = [{"name": source_directives[0][0], "kind": source_directives[0][0]}]
        elif not source_directives:
            errors.append("spec has no analyses and NET has no analysis directive")
        else:
            errors.append("NET contains multiple analysis directives; specify analyses explicitly")
    resolved_directives: dict[str, str] = {}
    for item in analyses:
        directive, error = resolve_analysis_directive(source_directives, item)
        if error:
            errors.append(error)
        elif directive:
            resolved_directives[str(item["name"]).lower()] = directive
            if str(item["kind"]).lower() == "dc":
                dc_error = _validate_dc_directive(directive)
                if dc_error:
                    errors.append(f"analysis {item['name']}: {dc_error}")

    raw_metrics = spec.get("metrics", {})
    if raw_metrics is None:
        raw_metrics = {}
    if not isinstance(raw_metrics, dict):
        errors.append("metrics must be an object")
        raw_metrics = {}
    for metric_name, raw_metric in raw_metrics.items():
        if not isinstance(raw_metric, dict):
            errors.append(f"metric {metric_name} must be an object")
            continue
        kind = str(raw_metric.get("kind", "abs_max")).lower()
        if kind not in SCALAR_METRICS and kind not in AXIS_METRICS:
            errors.append(f"metric {metric_name}: unsupported kind {kind}")
        if not str(raw_metric.get("trace", "")).strip():
            errors.append(f"metric {metric_name}: trace is required")
        if kind in REFERENCE_METRICS and not str(raw_metric.get("reference", "")).strip():
            errors.append(f"metric {metric_name}: reference is required for {kind}")
        target_analysis = _metric_analysis_target(raw_metric, analyses)
        if target_analysis is None:
            errors.append(f"metric {metric_name}: analysis is missing or ambiguous")
        elif str(target_analysis["kind"]).lower() == "op" and kind in AXIS_METRICS:
            errors.append(f"metric {metric_name}: {kind} needs an analysis axis and cannot use .op")
        if kind in {"value_at", "value_at_x", "gain_at", "gain_at_frequency"} and "x" not in raw_metric:
            errors.append(f"metric {metric_name}: x is required for {kind}")
        for numeric_field in ("x", "target", "tolerance_percent", "min", "max"):
            if numeric_field in raw_metric:
                try:
                    numeric_value = parse_number(raw_metric[numeric_field])
                    if numeric_field == "tolerance_percent" and numeric_value < 0:
                        errors.append(f"metric {metric_name}: tolerance_percent must not be negative")
                except ValueError:
                    errors.append(f"metric {metric_name}: {numeric_field} must be numeric")
        if "min" in raw_metric and "max" in raw_metric:
            try:
                if parse_number(raw_metric["min"]) > parse_number(raw_metric["max"]):
                    errors.append(f"metric {metric_name}: min must not exceed max")
            except ValueError:
                pass
        if kind in {"fc_3db", "cutoff_3db"} and str(raw_metric.get("response", "lowpass")).lower() not in {"lowpass", "highpass"}:
            errors.append(f"metric {metric_name}: response must be lowpass or highpass")

    raw_corners = spec.get("corners") or spec.get("sweep")
    if raw_corners is not None and not isinstance(raw_corners, (list, dict)):
        errors.append("corners/sweep must be a list or object")
    if isinstance(raw_corners, dict):
        for key in raw_corners:
            if str(key).lower() not in params:
                errors.append(f"corner parameter does not exist: {key}")
    elif isinstance(raw_corners, list):
        for index, corner in enumerate(raw_corners):
            if not isinstance(corner, dict):
                errors.append(f"corner {index} must be an object")
                continue
            corner_params = corner.get("params", {})
            if not isinstance(corner_params, dict):
                errors.append(f"corner {index}.params must be an object")
            else:
                for key in corner_params:
                    if str(key).lower() not in params:
                        errors.append(f"corner parameter does not exist: {key}")
            selected = corner.get("analysis")
            if selected is not None and not any(str(selected).lower() in {
                str(item.get("name", "")).lower(), str(item.get("kind", "")).lower()
            } for item in analyses):
                errors.append(f"corner {index} references unknown analysis: {selected}")

    strategy = str(spec.get("corner_strategy", "auto")).lower()
    if strategy not in {"auto", "cartesian", "monotonic"}:
        errors.append(f"unsupported corner_strategy: {strategy}")
    monotonic = spec.get("monotonic")
    if monotonic is not None and not isinstance(monotonic, dict):
        errors.append("monotonic declarations must be an object")
    if strategy == "monotonic" and not monotonic:
        errors.append("corner_strategy=monotonic requires monotonic declarations")
    if isinstance(monotonic, dict):
        for objective, declaration in monotonic.items():
            if not isinstance(declaration, dict):
                continue
            selected = declaration.get("analysis")
            if selected is not None and not any(str(selected).lower() in {
                str(item.get("name", "")).lower(), str(item.get("kind", "")).lower()
            } for item in analyses):
                errors.append(f"monotonic declaration {objective} references unknown analysis: {selected}")

    if raw_corners is not None and not errors:
        try:
            planned_corners = expand_corners(
                source_text,
                raw_corners,
                monotonic=monotonic,
                strategy=strategy,
            )
            for index, corner in enumerate(planned_corners):
                params_for_corner = corner.get("params", {})
                if not isinstance(params_for_corner, dict):
                    errors.append(f"corner {index}.params must be an object")
                    continue
                replace_parameters(source_text, params_for_corner)
        except (TypeError, ValueError) as exc:
            errors.append(f"corner plan: {exc}")

    hints = spec.get("convergence_hints", [])
    if hints is not None and not isinstance(hints, list):
        errors.append("convergence_hints must be a list")
    elif isinstance(hints, list):
        for hint in hints:
            if not isinstance(hint, str) or not hint.strip().lower().startswith(CONVERGENCE_HINT_PREFIXES):
                errors.append(f"unsupported convergence hint: {hint}")
            elif hint.strip().lower().startswith(".options") and "uic" in hint.lower():
                errors.append("UIC is not an allowed default convergence hint")

    try:
        timeout_seconds = float(spec.get("timeout_seconds", 120.0))
        if timeout_seconds <= 0:
            errors.append("timeout_seconds must be positive")
    except (TypeError, ValueError):
        errors.append("timeout_seconds must be numeric")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "analysis_count": len(analyses),
        "resolved_directives": resolved_directives,
        "dependency_manifest": dependencies,
        "convergence_hints": hints or [],
        "timeout_seconds": spec.get("timeout_seconds", 120.0),
    }


def coalesce_analyses(analyses: list[dict[str, object]], source_directives: list[tuple[str, str]]) -> list[dict[str, object]]:
    """Group duplicate nominal analyses so one RAW can answer many metrics."""

    grouped: dict[tuple[str, str], dict[str, object]] = {}
    order: list[dict[str, object]] = []
    for item in analyses:
        directive, _ = resolve_analysis_directive(source_directives, item)
        kind = str(item["kind"]).lower()
        key = (kind, (directive or str(item.get("directive") or "")).casefold())
        existing = grouped.get(key)
        if existing is None:
            existing = dict(item)
            existing["aliases"] = [str(item["name"])]
            grouped[key] = existing
            order.append(existing)
        else:
            aliases = list(existing.get("aliases", []))
            aliases.append(str(item["name"]))
            existing["aliases"] = aliases
    return order


def replace_parameters(text: str, params: dict[str, object]) -> str:
    return support_replace_parameters(text, params)


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


def _cartesian_corners(source_text: str, raw_corners: dict[str, object]) -> list[dict[str, object]]:
    base_values, parse_errors = parameter_values(source_text)
    if parse_errors:
        raise ValueError("; ".join(parse_errors))
    numeric_values: dict[str, float] = {}
    for key, item in base_values.items():
        try:
            numeric_values[key] = parse_number(item["value"])
        except ValueError:
            continue
    items = list(raw_corners.items())
    choices: list[list[tuple[str, object]]] = []
    for key, bounds in items:
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise ValueError(f"corner bounds for {key} must be [low_percent, high_percent]")
        normalized = str(key).lower()
        if normalized not in numeric_values:
            raise ValueError(f"corner parameter {key} has no numeric .param base value")
        try:
            low_percent, high_percent = float(bounds[0]), float(bounds[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"corner bounds for {key} must be numeric percentages") from exc
        base = numeric_values[normalized]
        choices.append([
            (f"{key}={bounds[0]}%", base * (1 + low_percent / 100.0)),
            (f"{key}={bounds[1]}%", base * (1 + high_percent / 100.0)),
        ])
    result = []
    for combination in itertools.product(*choices):
        params = {str(label).split("=", 1)[0]: value for label, value in combination}
        name = "corner-" + "-".join(str(label).replace("=", "_").replace("%", "pct") for label, _ in combination)
        result.append({"name": name[:96], "params": params, "analysis": None, "strategy": "cartesian"})
    return result


def _monotonic_corners(
    source_text: str,
    raw_corners: dict[str, object],
    monotonic: object,
) -> list[dict[str, object]]:
    if not isinstance(monotonic, dict) or not monotonic:
        raise ValueError("corner_strategy=monotonic requires a non-empty monotonic mapping")
    base_values, parse_errors = parameter_values(source_text)
    if parse_errors:
        raise ValueError("; ".join(parse_errors))
    numeric_values: dict[str, float] = {}
    for key, item in base_values.items():
        try:
            numeric_values[key] = parse_number(item["value"])
        except ValueError:
            continue
    bounds: dict[str, tuple[float, float, str]] = {}
    for key, raw_bounds in raw_corners.items():
        if not isinstance(raw_bounds, list) or len(raw_bounds) != 2:
            raise ValueError(f"corner bounds for {key} must be [low_percent, high_percent]")
        normalized = str(key).lower()
        if normalized not in numeric_values:
            raise ValueError(f"corner parameter {key} has no numeric .param base value")
        try:
            low_percent, high_percent = float(raw_bounds[0]), float(raw_bounds[1])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"corner bounds for {key} must be numeric percentages") from exc
        base = numeric_values[normalized]
        bounds[normalized] = (
            base * (1 + low_percent / 100.0),
            base * (1 + high_percent / 100.0),
            str(key),
        )

    result: list[dict[str, object]] = []
    seen: set[tuple[tuple[str, float], ...]] = set()
    for metric_name, declaration in monotonic.items():
        if isinstance(declaration, dict) and isinstance(declaration.get("parameters"), dict):
            directions = declaration["parameters"]
            analysis = declaration.get("analysis")
        elif isinstance(declaration, dict):
            directions = declaration
            analysis = None
        else:
            raise ValueError(f"monotonic declaration for {metric_name} must be an object")
        if set(str(key).lower() for key in directions) != set(bounds):
            raise ValueError(f"monotonic declaration for {metric_name} must cover every corner parameter")
        for extreme in ("max", "min"):
            params: dict[str, float] = {}
            for normalized, (low, high, original) in bounds.items():
                direction = str(directions.get(original, directions.get(normalized, ""))).lower()
                if direction in {"direct", "increasing", "positive"}:
                    use_high = extreme == "max"
                elif direction in {"inverse", "decreasing", "negative"}:
                    use_high = extreme == "min"
                elif direction in {"constant", "none", "irrelevant"}:
                    use_high = False
                else:
                    raise ValueError(f"invalid monotonic direction for {metric_name}.{original}: {direction}")
                params[original] = high if use_high else low
            signature = tuple(sorted((key.lower(), value) for key, value in params.items()))
            if signature in seen:
                continue
            seen.add(signature)
            result.append({
                "name": f"objective-{metric_name}-{extreme}",
                "params": params,
                "analysis": analysis,
                "strategy": "monotonic",
                "objective": str(metric_name),
                "extreme": extreme,
            })
    return result


def expand_corners(
    source_text: str,
    raw_corners: object,
    *,
    monotonic: object = None,
    strategy: str = "auto",
) -> list[dict[str, object]]:
    if not raw_corners:
        return []
    if isinstance(raw_corners, list):
        result = []
        for index, item in enumerate(raw_corners):
            if not isinstance(item, dict):
                raise ValueError("corner list entries must be objects")
            result.append({"name": str(item.get("name", f"corner-{index + 1}")),
                           "params": dict(item.get("params", {})),
                           "analysis": item.get("analysis"), "strategy": "explicit"})
        return result
    if not isinstance(raw_corners, dict):
        raise ValueError("corners must be a list or an object")
    normalized_strategy = str(strategy or "auto").lower()
    if normalized_strategy not in {"auto", "cartesian", "monotonic"}:
        raise ValueError(f"unsupported corner_strategy: {strategy}")
    if normalized_strategy in {"auto", "monotonic"} and monotonic:
        return _monotonic_corners(source_text, raw_corners, monotonic)
    if normalized_strategy == "monotonic":
        raise ValueError("corner_strategy=monotonic requires monotonic declarations")
    return _cartesian_corners(source_text, raw_corners)


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
        try:
            raw_axis = raw.get_axis()
        except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
            raw_axis = None
        axis = np.asarray(raw_axis) if raw_axis is not None else np.asarray([])
    if axis.size == 0 and values:
        axis = np.arange(len(next(iter(values.values()))), dtype=float)
    return axis, values


def metric_value(spec: dict[str, object], axis: np.ndarray, values: dict[str, np.ndarray]) -> float:
    kind = str(spec.get("kind", "abs_max")).lower()
    trace = str(spec.get("trace", ""))
    data = np.asarray(values[trace]).reshape(-1)
    real = np.real(data)
    if data.size == 0:
        raise ValueError("trace has no samples")
    if kind in {"value", "scalar"}:
        return float(real[-1])
    if kind in {"abs", "absolute"}:
        return float(abs(real[-1]))
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


def metric_specs_for_job(
    specs: object,
    job_name: str,
    job_kind: str,
    aliases: list[str] | None = None,
) -> dict[str, dict[str, object]]:
    if not isinstance(specs, dict):
        return {}
    selected: dict[str, dict[str, object]] = {}
    job_aliases = {job_name.lower(), job_kind.lower()}
    job_aliases.update(str(item).lower() for item in (aliases or []))
    for name, raw in specs.items():
        if not isinstance(raw, dict):
            continue
        target = raw.get("analysis")
        target_name = str(target).lower() if target is not None else None
        job_lower = job_name.lower()
        if target_name is None or target_name in job_aliases or job_lower.endswith("__" + target_name):
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
        if tolerance < 0:
            return False, f"tolerance_percent {spec.get('tolerance_percent')} must not be negative"
        if abs(target) > np.finfo(float).tiny:
            delta = abs(target) * tolerance
            lower, upper = target - delta, target + delta
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
    result["ok"] = bool(result.get("ok")) and completed.returncode == 0
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


def retire_existing_artifact(path: Path) -> None:
    """Remove an output sidecar before a new simulation can recreate it."""

    try:
        path.unlink()
    except FileNotFoundError:
        return


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


def compact_agent_summary(summary: dict[str, object]) -> dict[str, object]:
    """Build the small result surface an Agent should read first."""

    metrics: dict[str, object] = {}
    raw_metrics = summary.get("metrics", {})
    if isinstance(raw_metrics, dict):
        for name, item in raw_metrics.items():
            if not isinstance(item, dict):
                continue
            compact: dict[str, object] = {
                "value": item.get("value"),
                "ok": bool(item.get("ok")),
            }
            if not compact["ok"] and item.get("reason"):
                compact["reason"] = item.get("reason")
            metrics[str(name)] = compact
    artifacts = summary.get("artifact_paths", {})
    dry_run = summary.get("dry_run")
    dry_run_summary = None
    if isinstance(dry_run, dict):
        dry_run_summary = {
            "ok": bool(dry_run.get("ok")),
            "errors": list(dry_run.get("errors", []) or []),
            "warnings": list(dry_run.get("warnings", []) or []),
        }
    return {
        "status": summary.get("status") or ("PASS" if summary.get("ok") else "FAIL"),
        "ok": bool(summary.get("ok")),
        "dry_run": dry_run_summary,
        "ltspice_runs": int(summary.get("ltspice_runs", 0)),
        "evidence_reused": int(summary.get("evidence_reused", 0)),
        "evidence_reuse_disabled": int(summary.get("evidence_reuse_disabled", 0)),
        "convergence_retries": int(summary.get("convergence_retries", 0)),
        "simulation_fail_fast": bool(summary.get("simulation_fail_fast", True)),
        "simulation_fail_fast_triggered": bool(summary.get("simulation_fail_fast_triggered", False)),
        "simulation_failures": list(summary.get("simulation_failures", []) or []),
        "analysis_count": len(summary.get("analyses", []) or []),
        "corner_count": len(summary.get("corners", []) or []),
        "failed_corners": list(summary.get("failed_corners", []) or []),
        "metrics": metrics,
        "artifact_paths": dict(artifacts) if isinstance(artifacts, dict) else {},
        "failures": list(summary.get("failures", []) or []),
    }


class DryRunComplete(Exception):
    """Internal control flow for --dry-run and failed static validation."""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic LTspice analyses, corners, metrics, and summary generation.")
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path, help="Small JSON validation specification")
    parser.add_argument("--ltspice", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Circuit output folder; defaults to the NET folder")
    parser.add_argument("--summary", type=Path, help="JSON summary path; defaults to output/validation_summary.json")
    parser.add_argument("--markdown", type=Path, help="Optional human-readable summary path")
    parser.add_argument("--dry-run", action="store_true", help="Run only static validation-spec checks; never invoke LTspice")
    parser.add_argument("--verbose-json", action="store_true", help="Print the complete validation summary JSON to stdout")
    args = parser.parse_args()

    net = args.net.resolve()
    spec_path = args.spec.resolve()
    output = (args.output or net.parent).resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary_path = (args.summary or output / "validation_summary.json").resolve()
    markdown_path = args.markdown.resolve() if args.markdown else None
    dry_run_path = output / f"{net.stem}.validation-dry-run.json"
    evidence_path = output / "simulation_evidence.json"
    started_wall = datetime.now(timezone.utc).isoformat()
    started_perf = time.perf_counter()
    summary: dict[str, Any] = {
        "schema_version": 2,
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
        "evidence_reused": 0,
        "evidence_reuse_disabled": 0,
        "convergence_retries": 0,
        "convergence_retry_evidence_keys": [],
        "simulation_fail_fast": True,
        "simulation_fail_fast_triggered": False,
        "simulation_fail_fast_after": None,
        "simulation_failures": [],
        "skipped_jobs": [],
        "model_readable_summary_count": 1,
        "artifact_paths": {
            "net": str(net),
            "validation_summary": str(summary_path),
            "dry_run": str(dry_run_path),
            "simulation_evidence": str(evidence_path),
        },
        "timing": {},
    }
    preflight: dict[str, object] | None = None
    preflight_seconds = 0.0

    try:
        if not net.is_file() or not spec_path.is_file():
            raise FileNotFoundError("NET or specification is missing")
        source_text = net.read_text(encoding="utf-8", errors="replace")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        summary["net_sha256"] = sha256(net)
        summary["spec_sha256"] = sha256(spec_path)
        if isinstance(spec, dict):
            summary["simulation_fail_fast"] = bool(spec.get("simulation_fail_fast", True))

        dry_run = dry_run_spec(source_text, net, spec)
        dry_run_path.write_text(json.dumps(dry_run, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        summary["dry_run"] = dry_run
        if not dry_run.get("ok"):
            summary["failures"] = [f"spec-dry-run:{item}" for item in dry_run.get("errors", [])]
            summary["ok"] = False
            raise DryRunComplete()
        if args.dry_run:
            summary["dry_run_only"] = True
            summary["ok"] = True
            summary["status"] = "DRY_RUN_PASS"
            raise DryRunComplete()
        if not isinstance(spec, dict):
            raise ValueError("validation specification must be a JSON object")

        all_failures: list[str] = []
        source_directives = source_analysis_directives(source_text)
        analyses = [normalize_analysis(item) for item in spec.get("analyses", [])]
        if not analyses:
            analyses = [{"name": source_directives[0][0], "kind": source_directives[0][0]}]
        analyses = coalesce_analyses(analyses, source_directives)
        source_kinds = [kind for kind, _ in source_directives]
        exact_kind = source_kinds[0] if len(source_kinds) == 1 else None
        primary = analyses[0]
        if exact_kind and any(str(item["kind"]).lower() == exact_kind for item in analyses):
            primary = next(item for item in analyses if str(item["kind"]).lower() == exact_kind)

        if bool(spec.get("preflight", False)):
            state_path = output / ".validation-state.json"
            required_nets = [str(item) for item in spec.get("required_nets", [])]
            state_key = json_hash({
                "net": summary["net_sha256"],
                "required_nets": sorted(item.lower() for item in required_nets),
                "preflight_version": PREFLIGHT_VERSION,
            })
            summary["preflight_cache_key"] = state_key
            reuse = None
            if state_path.is_file():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if state.get("preflight_key") == state_key:
                        cached = state.get("preflight")
                        if isinstance(cached, dict) and preflight_result_ok(cached):
                            reuse = cached
                except (OSError, json.JSONDecodeError):
                    reuse = None
            preflight_path = output / f"{net.stem}.preflight.json"
            preflight, preflight_seconds = run_preflight(net, preflight_path, required_nets, reuse)
            if reuse is None:
                state_path.write_text(json.dumps({
                    "preflight_key": state_key,
                    "preflight_version": PREFLIGHT_VERSION,
                    "preflight": preflight,
                }, indent=2) + "\n", encoding="utf-8")
            summary["preflight"] = preflight
            summary["artifact_paths"]["preflight"] = str(preflight_path)
            if not preflight_result_ok(preflight):
                summary["failures"] = ["preflight:failed"]
                summary["status"] = "PREFLIGHT_FAIL"
                raise DryRunComplete()

        dependencies = dependency_manifest(net, source_text)
        evidence = EvidenceStore(evidence_path)
        summary["artifact_paths"]["simulation_evidence"] = str(evidence_path)

        with tempfile.TemporaryDirectory(prefix=".validation-suite-", dir=str(output)) as work_text:
            work = Path(work_text)
            timeout_seconds = float(spec.get("timeout_seconds", 120.0))
            convergence_hints = list(spec.get("convergence_hints", []) or [])

            def execute_job(
                job_name: str,
                analysis: dict[str, object],
                params: dict[str, object],
                exact: bool,
                corner: str | None = None,
            ) -> dict[str, object]:
                job_start = time.perf_counter()
                kind = str(analysis["kind"]).lower()
                directive, _ = resolve_analysis_directive(source_directives, analysis)
                analysis_key = dict(analysis)
                if directive:
                    analysis_key["directive"] = directive
                if exact and not params:
                    rendered_text = source_text
                    prefix = net.stem
                else:
                    rendered_text = render_analysis_net(source_text, analysis_key, params)
                    prefix = f"{net.stem}__{job_name}"
                raw_destination = output / f"{prefix}.raw"
                log_destination = output / f"{prefix}.log"
                report_destination = output / f"{prefix}.run-report.json"
                simulation_input = simulation_evidence_payload(
                    source_net_sha256=str(summary["net_sha256"]),
                    rendered_text=rendered_text,
                    analysis=analysis_key,
                    params=params,
                    dependencies=dependencies,
                    executable=args.ltspice,
                )
                base_evidence_key = json_hash(simulation_input)
                final_evidence_key = base_evidence_key
                final_simulation_input = simulation_input
                convergence_retry_evidence_key: str | None = None
                convergence_retry_attempted = False
                if not dependencies.get("reuse_allowed", True):
                    summary["evidence_reuse_disabled"] += 1
                result = evidence.reuse(
                    base_evidence_key,
                    raw_destination,
                    log_destination,
                    simulation_input=simulation_input,
                )
                if result is not None:
                    summary["evidence_reused"] += 1
                    result["evidence_key"] = base_evidence_key
                    result["base_evidence_key"] = base_evidence_key
                    result["simulation_input"] = simulation_input
                    result["convergence_hints_used"] = False
                    result["convergence_retry_attempted"] = False
                    result["run_report"] = str(report_destination) if report_destination.is_file() else result.get("run_report")
                else:
                    retire_existing_artifact(raw_destination)
                    retire_existing_artifact(log_destination)
                    retire_existing_artifact(report_destination)
                    if exact and not params:
                        job_net = net
                    else:
                        job_dir = work / f"{prefix}-stage"
                        job_net = stage_net_with_dependencies(net, rendered_text, job_dir, dependencies)
                    run_report = work / f"{prefix}.run-report.json"
                    result = run_simulation(
                        job_net, args.ltspice, run_report,
                        timeout_seconds=timeout_seconds,
                    )
                    summary["ltspice_runs"] += 1
                    if result.get("timed_out") and convergence_hints:
                        convergence_retry_attempted = True
                        summary["convergence_retries"] += 1
                        retry_text = inject_validation_hints(rendered_text, convergence_hints)
                        retry_dir = work / f"{prefix}-convergence-stage"
                        retry_net = stage_net_with_dependencies(net, retry_text, retry_dir, dependencies)
                        retry_report = work / f"{prefix}-convergence.run-report.json"
                        retry_input = simulation_evidence_payload(
                            source_net_sha256=str(summary["net_sha256"]),
                            rendered_text=retry_text,
                            analysis=analysis_key,
                            params=params,
                            dependencies=dependencies,
                            executable=args.ltspice,
                        )
                        retry_input["convergence"] = {
                            "retry": True,
                            "hints": list(convergence_hints),
                        }
                        convergence_retry_evidence_key = json_hash(retry_input)
                        final_evidence_key = convergence_retry_evidence_key
                        final_simulation_input = retry_input
                        summary["convergence_retry_evidence_keys"].append(convergence_retry_evidence_key)
                        retry_result = evidence.reuse(
                            convergence_retry_evidence_key,
                            raw_destination,
                            log_destination,
                            simulation_input=retry_input,
                        )
                        if retry_result is not None:
                            summary["evidence_reused"] += 1
                            result = retry_result
                            result["reused"] = True
                            result["convergence_hints_used"] = list(convergence_hints)
                            result["convergence_retry_attempted"] = True
                            run_report = Path(str(result.get("run_report", report_destination)))
                        else:
                            result = run_simulation(
                                retry_net, args.ltspice, retry_report,
                                timeout_seconds=timeout_seconds,
                            )
                            summary["ltspice_runs"] += 1
                            result["convergence_hints_used"] = list(convergence_hints)
                            result["convergence_retry_attempted"] = True
                            run_report = retry_report
                    run_input = Path(str(result.get("run_input", job_net)))
                    if not result.get("reused"):
                        raw_source = Path(str(result.get("raw", run_input.with_suffix(".raw"))))
                        log_source = Path(str(result.get("log", run_input.with_suffix(".log"))))
                        copy_artifact(raw_source, raw_destination)
                        copy_artifact(log_source, log_destination)
                        copy_artifact(run_report, report_destination)
                    result["raw"] = str(raw_destination) if raw_destination.is_file() else None
                    result["log"] = str(log_destination) if log_destination.is_file() else None
                    if report_destination.is_file():
                        result["run_report"] = str(report_destination)
                    result["reused"] = bool(result.get("reused", False))
                    result["evidence_key"] = final_evidence_key
                    result["base_evidence_key"] = base_evidence_key
                    result["convergence_retry_evidence_key"] = convergence_retry_evidence_key
                    result["convergence_hints_used"] = list(result.get("convergence_hints_used", []) or [])
                    result["convergence_retry_attempted"] = convergence_retry_attempted
                    result["original_evidence_key"] = base_evidence_key
                    result["actual_evidence_key"] = final_evidence_key
                    result["simulation_input"] = final_simulation_input
                    if result.get("ok") and raw_destination.is_file() and log_destination.is_file():
                        evidence.record_success(
                            final_evidence_key,
                            raw=raw_destination,
                            log=log_destination,
                            run_report=report_destination,
                            result=result,
                            simulation_input=final_simulation_input,
                        )
                        result["evidence_generated_at_utc"] = evidence.records[final_evidence_key].get("generated_at_utc")

                result["original_evidence_key"] = str(result.get("original_evidence_key") or base_evidence_key)
                result["actual_evidence_key"] = str(result.get("actual_evidence_key") or final_evidence_key)
                result["convergence_hints_used"] = list(result.get("convergence_hints_used", []) or [])
                raw_path = Path(str(result.get("raw", raw_destination)))
                log_path = Path(str(result.get("log", log_destination)))
                log_errors = list(result.get("errors", []))
                summary["log_error_status"][job_name] = {
                    "ok": not log_errors,
                    "errors": log_errors,
                    "reused_evidence": bool(result.get("reused")),
                }
                metric_specs = metric_specs_for_job(
                    spec.get("metrics", {}), job_name, kind,
                    aliases=[str(item) for item in analysis.get("aliases", [])],
                )
                metric_results: dict[str, object] = {}
                metric_failures: list[str] = []
                if result.get("ok") and raw_path.is_file() and metric_specs:
                    try:
                        metric_results, metric_failures = evaluate_metrics(raw_path, metric_specs)
                    except Exception as exc:
                        metric_failures = list(metric_specs)
                        metric_results = {
                            name: {"value": None, "ok": False, "reason": str(exc), "spec": metric}
                            for name, metric in metric_specs.items()
                        }
                elif metric_specs:
                    metric_failures = list(metric_specs)
                    metric_results = {
                        name: {
                            "value": None,
                            "ok": False,
                            "reason": "simulation failed; metric was not evaluated",
                            "spec": metric,
                        }
                        for name, metric in metric_specs.items()
                    }
                for name, value in metric_results.items():
                    summary["metrics"][f"{job_name}.{name}"] = value
                failures = log_errors + metric_failures
                return {
                    "name": job_name,
                    "kind": kind,
                    "aliases": list(analysis.get("aliases", [])),
                    "corner": corner,
                    "simulation_ok": bool(result.get("ok")),
                    "metric_failures": metric_failures,
                    "ok": bool(result.get("ok")) and not metric_failures,
                    "runtime_seconds": 0.0 if result.get("reused") else result.get("elapsed_seconds", round(time.perf_counter() - job_start, 6)),
                    "run": {
                        key_name: result.get(key_name)
                        for key_name in ("returncode", "fresh_raw", "fresh_log", "errors", "stale_raw", "stale_log", "timed_out", "convergence_hints_used", "convergence_retry_attempted", "convergence_retry_evidence_key", "base_evidence_key", "original_evidence_key", "actual_evidence_key", "reused", "evidence_key", "evidence_generated_at_utc")
                    },
                    "metrics": metric_results,
                    "artifacts": {
                        "input": str(net),
                        "raw": str(raw_path) if raw_path.is_file() else None,
                        "log": str(log_path) if log_path.is_file() else None,
                        "run_report": result.get("run_report"),
                    },
                    "failures": failures,
                }

            def collect_job(job: dict[str, object], *, corner: bool = False) -> bool:
                if not job["ok"]:
                    all_failures.extend([f"{job['name']}:{item}" for item in job["failures"]])
                if not job.get("simulation_ok"):
                    summary["simulation_failures"].append(str(job["name"]))
                    if summary["simulation_fail_fast"] and not summary["simulation_fail_fast_triggered"]:
                        summary["simulation_fail_fast_triggered"] = True
                        summary["simulation_fail_fast_after"] = str(job["name"])
                        return True
                return False

            stop_after_simulation_failure = False
            primary_job = execute_job(
                str(primary["name"]), primary, {},
                exact=can_use_exact_source(source_directives, primary),
            )
            summary["analyses"].append(primary_job)
            stop_after_simulation_failure = collect_job(primary_job)

            for analysis in analyses:
                if analysis is primary:
                    continue
                if stop_after_simulation_failure:
                    summary["skipped_jobs"].append(str(analysis["name"]))
                    continue
                job = execute_job(str(analysis["name"]), analysis, {}, exact=False)
                summary["analyses"].append(job)
                stop_after_simulation_failure = collect_job(job)

            corners = expand_corners(
                source_text,
                spec.get("corners") or spec.get("sweep"),
                monotonic=spec.get("monotonic"),
                strategy=str(spec.get("corner_strategy", "auto")),
            )
            summary["corner_plan"] = {
                "requested": spec.get("corners") or spec.get("sweep"),
                "strategy": str(spec.get("corner_strategy", "auto")),
                "count": len(corners),
            }
            corner_analysis = primary
            corner_start = time.perf_counter()
            for corner in corners:
                if stop_after_simulation_failure:
                    chosen = corner.get("analysis") or corner_analysis.get("name")
                    summary["skipped_jobs"].append(f"{corner['name']}__{chosen}")
                    continue
                chosen = corner.get("analysis")
                if chosen:
                    chosen_lower = str(chosen).lower()
                    analysis = next((item for item in analyses if chosen_lower in {
                        str(item["name"]).lower(), str(item["kind"]).lower(),
                        *(str(alias).lower() for alias in item.get("aliases", [])),
                    }), corner_analysis)
                else:
                    analysis = corner_analysis
                job_name = f"{corner['name']}__{analysis['name']}"
                job = execute_job(job_name, analysis, dict(corner.get("params", {})), exact=False, corner=str(corner["name"]))
                summary["corners"].append(job)
                if not job["ok"]:
                    summary["failed_corners"].append(str(corner["name"]))
                if collect_job(job, corner=True):
                    stop_after_simulation_failure = True
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
        summary["ok"] = (
            preflight_result_ok(preflight)
            and not all_failures
            and all(bool(item.get("ok")) for item in summary["analyses"] + summary["corners"])
        )
    except DryRunComplete:
        summary["timing"] = {"deterministic_tool_total_seconds": round(time.perf_counter() - started_perf, 6)}
    except Exception as exc:
        summary["failures"] = [str(exc)]
        summary["ok"] = False
        summary["timing"] = {"deterministic_tool_total_seconds": round(time.perf_counter() - started_perf, 6)}

    summary["status"] = summary.get("status") or ("PASS" if summary["ok"] else "FAIL")
    summary["artifact_paths"]["validation_summary"] = str(summary_path)
    if markdown_path:
        render_markdown(summary, markdown_path)
        summary["artifact_paths"]["verification_summary"] = str(markdown_path)
    summary["agent_summary"] = compact_agent_summary(summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.verbose_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(summary["agent_summary"], ensure_ascii=False, separators=(",", ":")))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
