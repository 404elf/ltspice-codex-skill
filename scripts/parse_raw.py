#!/usr/bin/env python3
"""Read LTspice RAW data with PyLTSpice and emit compact JSON statistics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PyLTSpice import RawRead


def resolve_trace(names: list[str], requested: str) -> str:
    for name in names:
        if name.lower() == requested.lower():
            return name
    raise KeyError(f"trace not found: {requested}; available={names}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse an LTspice .raw file with PyLTSpice.")
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--trace", action="append", help="Trace name; repeat for multiple traces")
    parser.add_argument("--json", type=Path, help="Optional JSON output path")
    args = parser.parse_args()

    raw_path = args.raw.resolve()
    if not raw_path.is_file():
        print(f"ERROR: RAW file missing: {raw_path}", file=sys.stderr)
        return 2
    raw = RawRead(raw_path, verbose=False)
    names = list(raw.get_trace_names())
    requested = args.trace or names
    stats: dict[str, object] = {}
    for item in requested:
        name = resolve_trace(names, item)
        values = np.asarray(raw.get_trace(name).get_wave())
        real = np.real(values)
        entry: dict[str, object] = {
            "samples": int(values.size),
            "min": float(np.min(real)),
            "max": float(np.max(real)),
            "first": float(real.flat[0]),
            "last": float(real.flat[-1]),
        }
        if np.iscomplexobj(values):
            magnitude = np.abs(values)
            entry["magnitude_max"] = float(np.max(magnitude))
        stats[name] = entry
    result = {
        "raw": str(raw_path),
        "plots": list(raw.get_plot_names()),
        "traces": names,
        "stats": stats,
    }
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.json:
        args.json.resolve().write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
