#!/usr/bin/env python3
"""Convert one exact netlist with Weave and require round-trip MATCH."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_NAME = ".ltspice-codex-config.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _resolve_path(value: object, base: Path) -> Path:
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _resolve_program(value: object, base: Path) -> str:
    text = str(value or "").strip()
    if not text or ("/" not in text and "\\" not in text and not Path(text).suffix):
        return text or "node"
    return str(_resolve_path(text, base))


def resolve_tools(
    weave_dir: Path | None,
    node: str | None,
    config_path: Path | None = None,
) -> tuple[Path, str]:
    """Resolve Weave and Node from explicit options or the local config."""

    config: dict[str, object] = {}
    config_file = (config_path or ROOT / CONFIG_NAME).resolve()
    if weave_dir is None or node is None or config_path is not None:
        if not config_file.is_file():
            if weave_dir is None:
                raise ValueError(f"configuration missing: {config_file}")
        else:
            try:
                loaded = json.loads(config_file.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"configuration invalid: {config_file}") from exc
            if not isinstance(loaded, dict):
                raise ValueError(f"configuration invalid: {config_file}")
            config = loaded
    if weave_dir is None:
        configured_weave = config.get("weave_cli") or config.get("weave_dir")
        if not configured_weave:
            raise ValueError("configuration missing weave_cli/weave_dir")
        weave_dir = _resolve_path(configured_weave, config_file.parent)
    else:
        weave_dir = weave_dir.resolve()
    node_value = node if node is not None else config.get("node", "node")
    return weave_dir, _resolve_program(node_value, config_file.parent)


def main() -> int:
    parser = argparse.ArgumentParser(description="Weave-convert and verify an exact netlist.")
    parser.add_argument("--net", required=True, type=Path)
    parser.add_argument("--weave-dir", type=Path, help="Override the configured Weave CLI directory")
    parser.add_argument("--node", help="Override the configured Node executable")
    parser.add_argument("--config", type=Path, help="Optional local Skill configuration")
    parser.add_argument("--asc", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    net = args.net.resolve()
    try:
        weave_dir, node = resolve_tools(args.weave_dir, args.node, args.config)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    weave_js = weave_dir / "weave.js"
    asc = (args.asc.resolve() if args.asc else net.with_suffix(".asc"))
    result_path = (args.result.resolve() if args.result else
                   net.with_name(f"{net.stem}.weave-verification.txt"))
    if not net.is_file() or not weave_js.is_file():
        print("ERROR: netlist or weave.js missing", file=sys.stderr)
        return 2
    if asc.parent != net.parent or result_path.parent != net.parent:
        print("ERROR: ASC and verification result must be beside the netlist", file=sys.stderr)
        return 2
    if asc.exists() and not args.force:
        print(f"ERROR: refusing to overwrite {asc}; use --force", file=sys.stderr)
        return 2

    before = sha256(net)
    conversion = run([node, str(weave_js), "convert", str(net), str(asc)], weave_dir)
    verification = None
    verdict = "ERROR"
    after = sha256(net)
    if conversion.returncode == 0 and asc.is_file() and after == before:
        verification = run([node, str(weave_js), "verify", str(net), str(asc)], weave_dir)
        match = verification.returncode == 0 and re.search(
            r"(?im)^\s*MATCH(?:\s|$)", verification.stdout or ""
        )
        verdict = "MATCH" if match else "MISMATCH"
    else:
        verdict = "NET_CHANGED_OR_CONVERSION_FAILED"

    lines = [
        "Weave netlist-to-ASC verification",
        f"INPUT_NET={net}",
        f"OUTPUT_ASC={asc}",
        f"NET_SHA256_BEFORE={before}",
        f"NET_SHA256_AFTER={after}",
        f"VERDICT={verdict}",
        f"CONVERSION_EXIT_CODE={conversion.returncode}",
        "CONVERSION_STDOUT_BEGIN", conversion.stdout.rstrip(), "CONVERSION_STDOUT_END",
        "CONVERSION_STDERR_BEGIN", conversion.stderr.rstrip(), "CONVERSION_STDERR_END",
    ]
    if verification is not None:
        lines.extend([
            f"VERIFICATION_EXIT_CODE={verification.returncode}",
            "VERIFICATION_STDOUT_BEGIN", verification.stdout.rstrip(), "VERIFICATION_STDOUT_END",
            "VERIFICATION_STDERR_BEGIN", verification.stderr.rstrip(), "VERIFICATION_STDERR_END",
        ])
    result_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"VERDICT={verdict}")
    print(f"ASC={asc}")
    print(f"RESULT={result_path}")
    return 0 if verdict == "MATCH" else 1


if __name__ == "__main__":
    raise SystemExit(main())
