#!/usr/bin/env python3
"""Run the existing validation suite from a small engineering intent.

This adapter owns only representation normalization, schema checks, and path
resolution.  Simulation, RAW/LOG validation, metrics, corners, and evidence
remain in run_validation_suite.py.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = ".ltspice-codex-config.json"
MODES = {"AUTO", "QUICK", "STANDARD", "STRICT", "BATCH"}
KINDS = {"tran", "ac", "dc", "op", "noise", "tf", "pz"}
MEASURE_ALIASES = {
    "p2p": "peak_to_peak",
    "cutoff": "fc_3db",
    "cutoff_3db": "fc_3db",
    "gain": "gain_at",
    "absolute_peak": "abs_max",
    "last": "final",
}
AXIS_METRICS = {"value_at", "value_at_x", "gain_at", "gain_at_frequency", "fc_3db", "cutoff_3db"}
REFERENCE_METRICS = {"gain_at", "gain_at_frequency"}


class IntentError(ValueError):
    """A deterministic, user-actionable intent error."""


def _text(value: object, label: str) -> str:
    if value is None or isinstance(value, bool):
        raise IntentError(f"{label} must be a non-empty string or number")
    result = str(value).strip()
    if not result:
        raise IntentError(f"{label} must not be empty")
    return result


def _number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise IntentError(f"{label} must be numeric")
    try:
        result = float(_text(value, label).removesuffix("%").strip())
    except ValueError as exc:
        raise IntentError(f"{label} must be numeric") from exc
    if result < 0:
        raise IntentError(f"{label} must not be negative")
    return result


def _take(data: dict[str, object], *names: str) -> object | None:
    found = [name for name in names if name in data]
    if len(found) > 1:
        raise IntentError(f"use only one of: {', '.join(found)}")
    return data.pop(found[0]) if found else None


def _resolve(value: object, base: Path) -> Path:
    candidate = Path(_text(value, "path")).expanduser()
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def _strip_json_comments(text: str) -> str:
    """Remove // and /* */ comments without changing quoted strings."""

    out: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(text):
        char = text[index]
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            out.append(char)
            index += 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    out: list[str] = []
    index = 0
    quote: str | None = None
    escaped = False
    while index < len(text):
        char = text[index]
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            out.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def parse_intent_text(text: str) -> object:
    """Accept JSON plus safe, mechanically recoverable JSON-like forms."""

    try:
        return json.loads(text.lstrip("\ufeff"))
    except json.JSONDecodeError as first_error:
        cleaned = _strip_trailing_commas(_strip_json_comments(text.lstrip("\ufeff")))
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            try:
                value = ast.literal_eval(cleaned)
            except (SyntaxError, ValueError, TypeError) as exc:
                raise IntentError(f"intent syntax is not valid JSON or a safe JSON-like form: {first_error.msg}") from exc
            if not isinstance(value, (dict, list)):
                raise IntentError("intent must be an object")
            return value


def load_config(config_path: Path | None = None) -> dict[str, Path]:
    path = (config_path or ROOT / CONFIG_NAME).resolve()
    if not path.is_file():
        raise IntentError(f"CONFIG_MISSING: {path}; run bootstrap.py or create the local configuration")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntentError(f"CONFIG_INVALID: {path}") from exc
    if not isinstance(raw, dict):
        raise IntentError("CONFIG_INVALID: configuration must be an object")
    result: dict[str, Path] = {}
    for key in ("python", "ltspice", "output_root"):
        if key not in raw:
            raise IntentError(f"CONFIG_INVALID: missing {key}")
        result[key] = _resolve(raw[key], path.parent)
    if not result["python"].is_file():
        raise IntentError(f"CONFIG_INVALID: python not found: {result['python']}")
    if not result["ltspice"].is_file():
        raise IntentError(f"CONFIG_INVALID: LTspice not found: {result['ltspice']}")
    result.update(config=path, root=ROOT)
    return result


def resolve_paths(net_value: object, config_path: Path | None = None, *, cwd: Path | None = None) -> dict[str, Path]:
    config = load_config(config_path)
    net = _resolve(net_value, (cwd or Path.cwd()).resolve())
    if not net.is_file():
        raise IntentError(f"NET_NOT_FOUND: {net}")
    root = config["output_root"]
    try:
        net.parent.relative_to(root)
        output = net.parent if net.parent != root else root / net.stem
    except ValueError:
        output = root / net.stem
    return {"net": net, "output": output.resolve(), **config}


def _analysis(label: str, raw: object) -> dict[str, object]:
    if isinstance(raw, str):
        data: dict[str, object] = {"directive": raw}
    elif raw is None:
        data = {}
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        raise IntentError(f"analyses.{label} must be an object, directive string, or null")
    name = _text(data.pop("name", label), f"analyses.{label}.name")
    direct = data.pop("directive", None)
    inferred = None
    if direct is not None:
        directive = _text(direct, f"analyses.{label}.directive")
        match = re.match(r"^\s*\.(tran|ac|dc|op|noise|tf|pz)\b", directive, re.I)
        if not match:
            raise IntentError(f"analyses.{label}.directive is not a supported analysis directive")
        inferred = match.group(1).lower()
    kind = _text(data.pop("kind", inferred or label), f"analyses.{label}.kind").lower()
    if kind not in KINDS:
        raise IntentError(f"analyses.{label}: unsupported kind {kind}")
    if inferred and inferred != kind:
        raise IntentError(f"analyses.{label}.directive must be a .{kind} directive")
    nested_requirements = data.pop("requirements", None)
    nested_tolerances = data.pop("tolerances", None)
    if data:
        raise IntentError(f"analyses.{label}: unsupported fields: {', '.join(sorted(data))}")
    result: dict[str, object] = {"name": name, "kind": kind, **({"directive": directive} if direct is not None else {})}
    if nested_requirements is not None:
        result["requirements"] = nested_requirements
    if nested_tolerances is not None:
        result["tolerances"] = nested_tolerances
    return result


def _analyses(raw: object) -> list[dict[str, object]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        match = re.match(r"^\s*\.(tran|ac|dc|op|noise|tf|pz)\b", raw, re.I)
        if not match:
            raise IntentError("analyses directive must start with .tran, .ac, .dc, .op, .noise, .tf, or .pz")
        kind = match.group(1).lower()
        return [_analysis(kind, {"kind": kind, "directive": raw})]
    if isinstance(raw, dict):
        if any(key in raw for key in ("name", "kind", "directive")):
            entries = [(str(raw.get("name") or raw.get("kind") or "analysis"), raw)]
        else:
            entries = list(raw.items())
    elif isinstance(raw, list):
        entries = []
        for index, value in enumerate(raw):
            if isinstance(value, str):
                match = re.match(r"^\s*\.(tran|ac|dc|op|noise|tf|pz)\b", value, re.I)
                label = match.group(1).lower() if match else value
                entries.append((label, value))
            elif isinstance(value, dict) and (value.get("name") or value.get("kind")):
                entries.append((str(value.get("name") or value.get("kind")), value))
            else:
                raise IntentError(f"analyses[{index}] requires a kind/name or directive")
    else:
        raise IntentError("analyses must be an object, list, or directive string")
    result = [_analysis(str(label), value) for label, value in entries]
    names = [str(item["name"]).casefold() for item in result]
    if len(names) != len(set(names)):
        raise IntentError("duplicate analysis name")
    return result


REQUIREMENT_FIELDS = {
    "name", "id", "measure", "kind", "signal", "trace", "analysis", "reference", "ref",
    "at", "x", "target", "min", "max", "response", "tolerance", "tolerance_percent",
}


def _analysis_name(label: object, analyses: list[dict[str, object]]) -> str | None:
    text = str(label).casefold()
    matches = [str(item["name"]) for item in analyses if text in {
        str(item["name"]).casefold(), str(item["kind"]).casefold(),
    }]
    return matches[0] if len(matches) == 1 else None


def _nested_requirement_items(label: object, raw: object, analyses: list[dict[str, object]]) -> list[dict[str, object]]:
    analysis = _analysis_name(label, analyses)
    if analysis is None:
        raise IntentError(f"requirements.{label}: unknown or ambiguous analysis")
    if isinstance(raw, dict) and any(key in raw for key in REQUIREMENT_FIELDS):
        entries = [(None, raw)]
    elif isinstance(raw, list):
        entries = [(None, item) for item in raw]
    elif isinstance(raw, dict):
        entries = list(raw.items())
    else:
        raise IntentError(f"requirements.{label} must be an object or list")
    result: list[dict[str, object]] = []
    for name, item in entries:
        if not isinstance(item, dict):
            raise IntentError(f"requirements.{label} entries must be objects")
        data = dict(item)
        if name is not None and not any(key in data for key in ("name", "id")):
            data["name"] = name
        data.setdefault("analysis", analysis)
        result.append(data)
    return result


def _requirement_items(raw: object, analyses: list[dict[str, object]]) -> list[object]:
    if raw is None:
        return []
    if isinstance(raw, dict) and any(key in raw for key in REQUIREMENT_FIELDS):
        return [raw]
    if isinstance(raw, list):
        return list(raw)
    if not isinstance(raw, dict):
        raise IntentError("requirements must be a list or named object")
    if raw and all(_analysis_name(key, analyses) for key in raw):
        result: list[object] = []
        for label, value in raw.items():
            result.extend(_nested_requirement_items(label, value, analyses))
        return result
    return [dict(value, name=name) if isinstance(value, dict) else value for name, value in raw.items()]


def _requirements(raw: object, analyses: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    raw_items = _requirement_items(raw, analyses)
    known = {str(value).casefold() for item in analyses for value in (item["name"], item["kind"])}
    analysis_kind = {str(item["name"]).casefold(): str(item["kind"]).lower() for item in analyses}
    result: dict[str, dict[str, object]] = {}
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise IntentError(f"requirements[{index}] must be an object")
        data = dict(raw_item)
        name = data.pop("name", data.pop("id", f"requirement_{index + 1}"))
        name = _text(name, f"requirements[{index}].name")
        if name in result:
            raise IntentError(f"duplicate requirement name: {name}")
        measure = _take(data, "measure", "kind")
        signal = _take(data, "signal", "trace")
        if measure is None or signal is None:
            raise IntentError(f"requirements.{name}: measure and signal are required")
        measure_text = _text(measure, f"requirements.{name}.measure").lower()
        metric_kind = MEASURE_ALIASES.get(measure_text, measure_text)
        metric: dict[str, object] = {"kind": metric_kind, "trace": _text(signal, f"requirements.{name}.signal")}
        selected = data.pop("analysis", None)
        if selected is None:
            if len(analyses) != 1:
                raise IntentError(f"requirements.{name}: analysis is required with multiple analyses")
            selected = analyses[0]["name"]
        selected_text = _text(selected, f"requirements.{name}.analysis")
        if known and selected_text.casefold() not in known:
            raise IntentError(f"requirements.{name}: unknown analysis {selected_text}")
        metric["analysis"] = selected_text
        reference = _take(data, "reference", "ref")
        if reference is not None:
            metric["reference"] = _text(reference, f"requirements.{name}.reference")
        at = _take(data, "at", "x")
        if at is not None:
            metric["x"] = at
        for key in ("target", "min", "max", "response"):
            if key in data:
                metric[key] = data.pop(key)
        tolerance = _take(data, "tolerance", "tolerance_percent")
        if tolerance is not None:
            metric["tolerance_percent"] = _number(tolerance, f"requirements.{name}.tolerance")
        elif "target" in metric:
            metric["tolerance_percent"] = 0.0
        if data:
            raise IntentError(f"requirements.{name}: unsupported fields: {', '.join(sorted(data))}")
        if metric_kind in AXIS_METRICS and "x" not in metric and metric_kind not in {"fc_3db", "cutoff_3db"}:
            raise IntentError(f"requirements.{name}: at is required for {metric_kind}")
        if metric_kind in REFERENCE_METRICS and "reference" not in metric:
            raise IntentError(f"requirements.{name}: reference is required for {metric_kind}")
        selected_kind = analysis_kind.get(selected_text.casefold(), selected_text.casefold())
        if selected_kind == "op" and metric_kind in AXIS_METRICS:
            raise IntentError(f"requirements.{name}: {metric_kind} needs an analysis axis and is invalid for .op")
        if selected_kind == "op" and metric_kind in {"value", "scalar"}:
            metric["kind"] = "final"
        elif selected_kind == "op" and metric_kind in {"abs", "absolute"}:
            metric["kind"] = "abs_max"
        result[name] = metric
    return result


def _tolerance_payload(raw: object, label: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise IntentError(f"{label} must be an object with parameters")
    data = dict(raw)
    strategy = _text(data.pop("strategy", data.pop("corner_strategy", "auto")), "tolerances.strategy").lower()
    if strategy not in {"auto", "cartesian", "monotonic"}:
        raise IntentError(f"unsupported tolerance strategy: {strategy}")
    values = data.pop("parameters", None)
    if values is None:
        raise IntentError("tolerances.parameters must be a non-empty object")
    if not isinstance(values, dict) or not values:
        raise IntentError("tolerances.parameters must be a non-empty object")
    corners: dict[str, list[float]] = {}
    for name, value in values.items():
        if isinstance(value, dict):
            value = value.get("percent")
        percent = _number(value, f"tolerances.parameters.{name}")
        corners[str(name)] = [-percent, percent]
    result: dict[str, object] = {"corners": corners}
    if strategy != "auto":
        result["corner_strategy"] = strategy
    objectives = data.pop("objectives", None)
    if strategy == "monotonic":
        if not isinstance(objectives, dict) or not objectives:
            raise IntentError("monotonic tolerances require objectives")
        monotonic: dict[str, object] = {}
        for name, item in objectives.items():
            if not isinstance(item, dict):
                raise IntentError(f"tolerances.objectives.{name} must be an object")
            directions = item.get("directions", item.get("parameters"))
            if not isinstance(directions, dict):
                raise IntentError(f"tolerances.objectives.{name} requires directions")
            monotonic[str(name)] = {"parameters": directions, **({"analysis": item["analysis"]} if "analysis" in item else {})}
        result["monotonic"] = monotonic
    elif objectives is not None:
        raise IntentError(f"{label}.objectives is only valid with strategy=monotonic")
    if data:
        raise IntentError(f"{label}: unsupported fields: {', '.join(sorted(data))}")
    return result


def _tolerance_analysis(label: object, analyses: list[dict[str, object]]) -> str:
    name = _analysis_name(label, analyses)
    if name is None:
        raise IntentError(f"tolerances.{label}: unknown or ambiguous analysis")
    return name


def _tolerances(raw: object, analyses: list[dict[str, object]]) -> dict[str, object]:
    if raw is None:
        return {}
    if isinstance(raw, list):
        groups = raw
    elif isinstance(raw, dict):
        reserved = {"parameters", "strategy", "corner_strategy", "objectives", "analysis"}
        if any(key in raw for key in reserved):
            if "analysis" in raw:
                group = dict(raw)
                analysis = _tolerance_analysis(group.pop("analysis"), analyses)
                payload = _tolerance_payload(group, "tolerances")
                payload["analysis"] = analysis
                return {"tolerance_groups": [payload]}
            return _tolerance_payload(raw, "tolerances")
        groups = raw.get("by_analysis", raw.get("per_analysis", raw.get("analyses")))
        if groups is None:
            groups = raw
        if not isinstance(groups, dict):
            raise IntentError("tolerances.by_analysis must be an object")
        normalized: list[dict[str, object]] = []
        for label, value in groups.items():
            payload = _tolerance_payload(value, f"tolerances.{label}")
            payload["analysis"] = _tolerance_analysis(label, analyses)
            normalized.append(payload)
        return {"tolerance_groups": normalized}
    else:
        raise IntentError("tolerances must be an object or list")

    normalized = []
    for index, item in enumerate(groups):
        if not isinstance(item, dict) or "analysis" not in item:
            raise IntentError(f"tolerances[{index}] requires analysis and parameters")
        data = dict(item)
        analysis = _tolerance_analysis(data.pop("analysis"), analyses)
        payload = _tolerance_payload(data, f"tolerances[{index}]")
        payload["analysis"] = analysis
        normalized.append(payload)
    return {"tolerance_groups": normalized}


def _model_policy(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        data = dict(raw)
        value = data.pop("policy", data.pop("name", None))
        if data:
            raise IntentError(f"model_policy: unsupported fields: {', '.join(sorted(data))}")
    else:
        value = raw
    policy = _text(value, "model_policy").lower()
    if policy != "real_device_required":
        raise IntentError(f"unsupported model_policy: {policy}")
    return policy


def normalize_intent(intent: object) -> dict[str, object]:
    if not isinstance(intent, dict):
        raise IntentError("intent must be an object")
    intent = dict(intent)
    wrapper_keys = [key for key in ("intent", "validation_intent") if key in intent]
    if len(wrapper_keys) > 1:
        raise IntentError("use only one of: intent, validation_intent")
    wrapper = intent.pop(wrapper_keys[0]) if wrapper_keys else None
    if wrapper is not None:
        if intent:
            raise IntentError("intent wrapper cannot be combined with sibling fields")
        if not isinstance(wrapper, dict):
            raise IntentError("intent wrapper must contain an object")
        intent = dict(wrapper)
    aliases = {"analysis": "analyses", "metrics": "requirements", "tolerance": "tolerances", "required_net": "required_nets"}
    for alias, canonical in aliases.items():
        if alias in intent:
            if canonical in intent:
                raise IntentError(f"use only one of: {alias}, {canonical}")
            intent[canonical] = intent.pop(alias)
    allowed = {"mode", "analyses", "requirements", "tolerances", "required_nets", "model_policy"}
    unknown = sorted(set(intent) - allowed)
    if unknown:
        raise IntentError(f"unsupported intent fields: {', '.join(unknown)}")
    mode = _text(intent.get("mode", "AUTO"), "mode").upper()
    if mode not in MODES:
        raise IntentError(f"unsupported mode: {mode}")
    analyses = _analyses(intent.get("analyses"))
    nested_requirements = [
        (str(item["name"]), item.pop("requirements"))
        for item in analyses
        if "requirements" in item
    ]
    nested_tolerances = [
        (str(item["name"]), item.pop("tolerances"))
        for item in analyses
        if "tolerances" in item
    ]
    if nested_requirements and "requirements" in intent:
        raise IntentError("use either top-level requirements or nested analysis requirements")
    if nested_tolerances and "tolerances" in intent:
        raise IntentError("use either top-level tolerances or nested analysis tolerances")
    requirements_raw = (
        {name: value for name, value in nested_requirements}
        if nested_requirements else intent.get("requirements")
    )
    tolerances_raw = (
        {name: value for name, value in nested_tolerances}
        if nested_tolerances else intent.get("tolerances")
    )
    spec: dict[str, object] = {
        "preflight": True,
        "simulation_fail_fast": True,
        "analyses": analyses,
        "metrics": _requirements(requirements_raw, analyses),
    }
    required = intent.get("required_nets")
    if required is not None:
        if isinstance(required, str):
            required = [required]
        if not isinstance(required, list) or not all(isinstance(item, str) and item.strip() for item in required):
            raise IntentError("required_nets must be a string or list of non-empty strings")
        spec["required_nets"] = [item.strip() for item in required]
    spec.update(_tolerances(tolerances_raw, analyses))
    model_policy = _model_policy(intent.get("model_policy"))
    if model_policy:
        spec["model_policy"] = model_policy
    return {"mode": mode, "spec": spec}


def _failure(stage: str, error: str, *, summary_path: Path | None = None) -> dict[str, object]:
    return {
        "status": "FAIL",
        "ok": False,
        "failure_class": "PLUMBING/INFRASTRUCTURE FAILURE",
        "failed_requirements": [],
        "summary_path": str(summary_path) if summary_path else None,
        "ltspice_calls": 0,
        "evidence_reused": 0,
        "stage": stage,
        "error": error,
    }


def _suite_json(stdout: str) -> dict[str, object] | None:
    text = stdout.strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    starts = [match.start() for match in re.finditer(r"\{", stdout)]
    for start in reversed(starts):
        try:
            value, _ = decoder.raw_decode(stdout[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _failure_class(summary: dict[str, object]) -> str:
    if bool(summary.get("ok")):
        return "NONE"
    failures = [str(item).lower() for item in summary.get("failures", []) or []]
    dry_run = summary.get("dry_run")
    if isinstance(dry_run, dict) and not dry_run.get("ok", True):
        if any("real device model required" in failure for failure in failures):
            return "ENGINEERING FAILURE"
        return "PLUMBING/INFRASTRUCTURE FAILURE"
    infrastructure_terms = (
        "no such", "unknown", "error", "fatal", "parser", "singular", "aborted",
        "missing", "fresh raw", "fresh log", "ltspice", "returncode", "timeout",
    )
    if any(any(term in failure for term in infrastructure_terms) for failure in failures):
        return "PLUMBING/INFRASTRUCTURE FAILURE"
    return "ENGINEERING FAILURE"


def _compact(summary: dict[str, object], fallback_summary: Path, mode: str) -> dict[str, object]:
    artifacts = summary.get("artifact_paths", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
    summary_path = Path(str(artifacts.get("validation_summary", fallback_summary)))
    failed: list[str] = []
    metrics = summary.get("metrics", {})
    if isinstance(metrics, dict):
        failed.extend(str(name) for name, value in metrics.items() if isinstance(value, dict) and not value.get("ok"))
    failed.extend(str(item) for item in summary.get("failed_corners", []) or [])
    if not failed:
        failed.extend(str(item) for item in summary.get("failures", []) or [])
    return {
        "status": "PASS" if bool(summary.get("ok")) else "FAIL",
        "ok": bool(summary.get("ok")),
        "failure_class": _failure_class(summary),
        "failed_requirements": failed,
        "summary_path": str(summary_path),
        "ltspice_calls": int(summary.get("ltspice_runs", 0) or 0),
        "evidence_reused": int(summary.get("evidence_reused", 0) or 0),
        "mode": mode,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run LTspice validation from a compact engineering intent.")
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--intent", required=True, type=Path)
    parser.add_argument("--config", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    paths: dict[str, Path] | None = None
    output: Path | None = None
    try:
        paths = resolve_paths(args.net, args.config)
        intent_path = _resolve(args.intent, Path.cwd().resolve())
        if not intent_path.is_file():
            raise IntentError(f"INTENT_NOT_FOUND: {intent_path}")
        normalized = normalize_intent(parse_intent_text(intent_path.read_text(encoding="utf-8-sig")))
        output = paths["output"]
        output.mkdir(parents=True, exist_ok=True)
        spec_path = output / "validation_spec.json"
        spec_path.write_text(json.dumps(normalized["spec"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, IntentError) as exc:
        print(json.dumps(_failure("intent", str(exc)), ensure_ascii=False, separators=(",", ":")))
        return 2

    summary_path = output / "validation_summary.json"
    command = [
        str(paths["python"]), str(paths["root"] / "scripts" / "run_validation_suite.py"),
        "--net", str(paths["net"]), "--spec", str(spec_path), "--ltspice", str(paths["ltspice"]),
        "--output", str(output), "--markdown", str(output / "validation_summary.md"),
    ]
    try:
        completed = subprocess.run(command, cwd=str(paths["root"]), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        print(json.dumps(_failure("suite", f"VALIDATION_TOOL_ERROR: {exc}", summary_path=summary_path),
                         ensure_ascii=False, separators=(",", ":")))
        return 2

    summary = _suite_json(completed.stdout)
    if summary is None:
        result = _failure("suite", f"validation suite produced no result (exit {completed.returncode})",
                          summary_path=summary_path)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 2
    result = _compact(summary, summary_path, str(normalized["mode"]))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] and completed.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
