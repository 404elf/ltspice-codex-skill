#!/usr/bin/env python3
"""Validate one fresh LTspice log without treating exit code as proof of success."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


RECOVERY_SUCCESS_RE = re.compile(
    r"(?:gmin\s+stepping|source\s+stepping)\s+succeeded\s+in\s+finding\s+"
    r"(?:the\s+)?operating\s+point",
    re.IGNORECASE,
)


def find_errors(text: str) -> list[str]:
    """Return lines that indicate an unresolved LTspice failure.

    LTspice may recover from a direct Newton operating-point failure with Gmin
    or source stepping. The direct Newton diagnostic is ignored only when a
    later line in the same log confirms that one of those methods found the
    operating point.
    """
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
    lines = text.splitlines()
    recovery_after = [False] * len(lines)
    seen_recovery = False
    for index in range(len(lines) - 1, -1, -1):
        recovery_after[index] = seen_recovery
        if RECOVERY_SUCCESS_RE.search(lines[index]):
            seen_recovery = True

    errors: list[str] = []
    for index, line in enumerate(lines):
        if recovery_after[index] and patterns[-1].search(line):
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

