#!/usr/bin/env python3
"""Validate one fresh LTspice log without treating exit code as proof of success."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def find_errors(text: str) -> list[str]:
    """Return lines that indicate an unresolved LTspice failure.

    LTspice can recover from a direct Newton operating-point failure with Gmin
    stepping. That diagnostic is ignored only when the same log confirms that
    Gmin stepping found the operating point.
    """

    recovered_gmin = bool(
        re.search(
            r"gmin\s+stepping\s+succeeded\s+in\s+finding\s+the\s+operating\s+point",
            text,
            re.IGNORECASE,
        )
    )
    patterns = (
        re.compile(r"^\s*(?:error|fatal)\b", re.IGNORECASE),
        re.compile(
            r"\b(?:no such|unknown|singular matrix|simulation\s+aborted|"
            r"voltage not found|not found)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b(?:parser error|parse error|failed to parse)\b", re.IGNORECASE),
        re.compile(r"\bdirect\s+newton\s+iteration\s+failed\s+to\s+find\s+"
                   r"(?:the\s+)?operating\s+point", re.IGNORECASE),
    )
    errors: list[str] = []
    for line in text.splitlines():
        if recovered_gmin and patterns[-1].search(line):
            continue
        if any(pattern.search(line) for pattern in patterns[:-1]):
            errors.append(line)
        elif patterns[-1].search(line):
            errors.append(line)
    return errors


def validate_log(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"ok": False, "log": str(path), "errors": ["log file missing"]}
    text = path.read_text(encoding="utf-8", errors="replace")
    errors = find_errors(text)
    return {"ok": not errors, "log": str(path), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check an LTspice log for unresolved failures.")
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--json", type=Path, help="Optional JSON result path.")
    args = parser.parse_args()
    result = validate_log(args.log.resolve())
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.json:
        args.json.resolve().write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
