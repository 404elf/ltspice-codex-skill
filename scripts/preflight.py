#!/usr/bin/env python3
"""Perform conservative text-level checks for STRICT-mode circuits."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


def terminals(tokens: list[str]) -> list[str]:
    if not tokens:
        return []
    prefix = tokens[0][0].upper()
    counts = {"R": 2, "C": 2, "L": 2, "D": 2, "V": 2, "I": 2, "B": 2,
              "E": 4, "F": 2, "G": 4, "H": 2, "J": 3, "M": 4}
    if prefix == "X":
        return tokens[1:-1]
    if prefix == "Q":
        # Q C B E [S] model [parameters].  The model token is the last
        # positional token before any name=value parameters.
        first_parameter = next((index for index, token in enumerate(tokens) if "=" in token), len(tokens))
        model_index = first_parameter - 1 if first_parameter < len(tokens) else len(tokens) - 1
        return tokens[1:max(1, model_index)]
    return tokens[1:1 + counts.get(prefix, 0)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run conservative STRICT-mode netlist checks.")
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--required-net", action="append", default=[])
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    net = args.net.resolve()
    if not net.is_file():
        print(f"ERROR: netlist missing: {net}", file=sys.stderr)
        return 2

    lines = net.read_text(encoding="utf-8", errors="replace").splitlines()
    active = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("*")]
    checks: list[dict[str, object]] = []
    checks.append({"name": "end_directive", "ok": any(x.lower() == ".end" for x in active)})
    checks.append({"name": "analysis_directive", "ok": any(x.lower().startswith((".tran", ".ac", ".op", ".dc")) for x in active)})
    placeholders = [line for line in lines if re.search(r"TODO|<[^>]+>|\{\{.+\}\}", line, re.IGNORECASE)]
    checks.append({"name": "no_placeholders", "ok": not placeholders, "details": placeholders})

    counts: Counter[str] = Counter()
    for line in active:
        if line.startswith("."):
            continue
        tokens = line.split()
        for node in terminals(tokens):
            if node.lower() not in {"0", "gnd"}:
                counts[node.lower()] += 1
    single_use = sorted(node for node, count in counts.items() if count == 1)
    checks.append({"name": "no_obvious_single_use_nets", "ok": not single_use, "details": single_use})
    missing = [name for name in args.required_net if name.lower() not in counts]
    checks.append({"name": "required_nets", "ok": not missing, "details": missing})
    result = {"net": str(net), "ok": all(bool(item["ok"]) for item in checks), "checks": checks}
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.json:
        args.json.resolve().write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
