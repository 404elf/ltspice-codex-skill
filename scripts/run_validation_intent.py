#!/usr/bin/env python3
"""Run the validation suite from a small, engineering-facing intent."""

from __future__ import annotations

import argparse
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
    "p2p": "peak_to_peak", "cutoff": "fc_3db", "cutoff_3db": "fc_3db",
    "gain": "gain_at", "absolute_peak": "abs_max", "last": "final",
}
AXIS_METRICS = {"value_at", "value_at_x", "gain_at", "gain_at_frequency"}
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


def load_config(config_path: Path | None = None) -> dict[str, Path]:
    path = (config_path or ROOT / CONFIG_NAME).resolve()
    if not path.is_file():
        raise IntentError(f"CONFIG_MISSING: {path}; run bootstrap.py")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntentError(f"CONFIG_INVALID: {path}") from exc
    if not isinstance(raw, dict):
        raise IntentError("CONFIG_INVALID: configuration must be an object")
    result: dict[str, Path] = {key: _resolve(raw[key], path.parent) for key in ("python", "ltspice", "output_root") if key in raw}
    missing = [key for key in ("python", "ltspice", "output_root") if key not in result]
    if missing:
        raise IntentError(f"CONFIG_INVALID: missing {', '.join(missing)}")
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
        same_tree = net.parent != root
    except ValueError:
        same_tree = False
    output = net.parent if same_tree else root / net.stem
    return {"net": net, "output": output.resolve(), **config}


def _analysis(label: str, raw: object) -> dict[str, object]:
    if isinstance(raw, str):
        data = {"directive": raw}
    elif raw is None:
        data = {}
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        raise IntentError(f"analyses.{label} must be an object, directive string, or null")
    name = _text(data.pop("name", label), f"analyses.{label}.name")
    kind = _text(data.pop("kind", label), f"analyses.{label}.kind").lower()
    if kind not in KINDS:
        raise IntentError(f"analyses.{label}: unsupported kind {kind}")
    direct = data.pop("directive", None)
    if direct is not None:
        directive = _text(direct, f"analyses.{label}.directive")
        match = re.match(r"^\s*\.(tran|ac|dc|op|noise|tf|pz)\b", directive, re.I)
        if not match or match.group(1).lower() != kind:
            raise IntentError(f"analyses.{label}.directive must be a .{kind} directive")
    else:
        directive = None
    if data:
        raise IntentError(f"analyses.{label}: unsupported fields: {', '.join(sorted(data))}")
    return {"name": name, "kind": kind, **({"directive": directive} if directive else {})}


def _analyses(raw: object) -> list[dict[str, object]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        entries = list(raw.items())
    elif isinstance(raw, list):
        entries = []
        for index, value in enumerate(raw):
            if not isinstance(value, dict) or not (value.get("name") or value.get("kind")):
                raise IntentError(f"analyses[{index}] requires name or kind")
            entries.append((str(value.get("name") or value.get("kind")), value))
    else:
        raise IntentError("analyses must be an object or list")
    result = [_analysis(str(label), value) for label, value in entries]
    names = [str(item["name"]).casefold() for item in result]
    if len(names) != len(set(names)):
        raise IntentError("duplicate analysis name")
    return result


def _requirements(raw: object, analyses: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise IntentError("requirements must be a list")
    known = {str(value).casefold() for item in analyses for value in (item["name"], item["kind"])}
    result: dict[str, dict[str, object]] = {}
    for index, raw_item in enumerate(raw):
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
        metric: dict[str, object] = {"kind": MEASURE_ALIASES.get(measure_text, measure_text),
                                     "trace": _text(signal, f"requirements.{name}.signal")}
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
        kind = str(metric["kind"])
        if kind in AXIS_METRICS and "x" not in metric:
            raise IntentError(f"requirements.{name}: at is required for {kind}")
        if kind in REFERENCE_METRICS and "reference" not in metric:
            raise IntentError(f"requirements.{name}: reference is required for {kind}")
        result[name] = metric
    return result


def _tolerances(raw: object) -> dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise IntentError("tolerances must be an object with parameters")
    strategy = _text(raw.get("strategy", "auto"), "tolerances.strategy").lower()
    if strategy not in {"auto", "cartesian", "monotonic"}:
        raise IntentError(f"unsupported tolerance strategy: {strategy}")
    values = raw.get("parameters")
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
    if strategy == "monotonic":
        objectives = raw.get("objectives")
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
    elif "objectives" in raw:
        raise IntentError("tolerances.objectives is only valid with strategy=monotonic")
    return result


def normalize_intent(intent: object) -> dict[str, object]:
    if not isinstance(intent, dict):
        raise IntentError("intent must be a JSON object")
    allowed = {"mode", "analyses", "requirements", "tolerances", "required_nets"}
    unknown = sorted(set(intent) - allowed)
    if unknown:
        raise IntentError(f"unsupported intent fields: {', '.join(unknown)}")
    mode = _text(intent.get("mode", "AUTO"), "mode").upper()
    if mode not in MODES:
        raise IntentError(f"unsupported mode: {mode}")
    analyses = _analyses(intent.get("analyses"))
    spec: dict[str, object] = {"preflight": True, "simulation_fail_fast": True,
                               "analyses": analyses, "metrics": _requirements(intent.get("requirements"), analyses)}
    required = intent.get("required_nets")
    if required is not None:
        if not isinstance(required, list) or not all(isinstance(item, str) and item.strip() for item in required):
            raise IntentError("required_nets must be a list of non-empty strings")
        spec["required_nets"] = [item.strip() for item in required]
    spec.update(_tolerances(intent.get("tolerances")))
    return {"mode": mode, "spec": spec}


def _failure(stage: str, error: str) -> dict[str, object]:
    return {"status": "FAIL", "ok": False, "stage": stage, "ltspice_runs": 0, "errors": [error]}


def _last_json(stdout: str) -> dict[str, object] | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run LTspice validation from an engineering intent.")
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--intent", required=True, type=Path)
    parser.add_argument("--config", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        paths = resolve_paths(args.net, args.config)
        intent_path = _resolve(args.intent, Path.cwd().resolve())
        normalized = normalize_intent(json.loads(intent_path.read_text(encoding="utf-8")))
        output = paths["output"]
        output.mkdir(parents=True, exist_ok=True)
        spec_path = output / "validation_spec.json"
        spec_path.write_text(json.dumps(normalized["spec"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, IntentError) as exc:
        print(json.dumps(_failure("intent", str(exc)), ensure_ascii=False, separators=(",", ":")))
        return 2
    command = [str(paths["python"]), str(paths["root"] / "scripts" / "run_validation_suite.py"),
               "--net", str(paths["net"]), "--spec", str(spec_path), "--ltspice", str(paths["ltspice"]),
               "--output", str(output), "--markdown", str(output / "validation_summary.md")]
    try:
        completed = subprocess.run(command, cwd=paths["root"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        print(json.dumps(_failure("suite", f"VALIDATION_TOOL_ERROR: {exc}"), ensure_ascii=False, separators=(",", ":")))
        return 2
    result = _last_json(completed.stdout) or _failure("suite", f"validation suite produced no compact result (exit {completed.returncode})")
    result.update(entrypoint="run_validation_intent", mode=normalized["mode"], canonical_spec=str(spec_path))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if bool(result.get("ok")) and completed.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
