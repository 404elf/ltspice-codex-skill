#!/usr/bin/env python3
"""Small deterministic helpers for validation evidence and temporary NETs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validate_log import find_errors


PARAM_LINE_RE = re.compile(r"^\s*\.param\b(?P<body>.*)$", re.IGNORECASE)
PARAM_ASSIGN_RE = re.compile(
    r"(?P<name>[A-Za-z_][\w]*)\s*=\s*"
    r"(?P<value>\{[^}]*\}|\"[^\"]*\"|'[^']*'|[^\s;]+)",
    re.IGNORECASE,
)
DEPENDENCY_LINE_RE = re.compile(
    r"^(?P<indent>\s*)\.(?P<kind>include|lib)\s+"
    r"(?P<target>\"[^\"]+\"|<[^>]+>|\S+)(?P<rest>.*)$",
    re.IGNORECASE,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(payload)


def parse_param_line(line: str) -> tuple[list[dict[str, str]], str | None]:
    """Parse one .param line and reject syntax this helper cannot edit safely."""

    match = PARAM_LINE_RE.match(line)
    if not match:
        return [], None
    body = match.group("body")
    body = body.split(";", 1)[0]
    assignments: list[dict[str, str]] = []
    cursor = 0
    for item in PARAM_ASSIGN_RE.finditer(body):
        gap = body[cursor:item.start()]
        if gap.strip():
            return assignments, f"unsupported .param syntax near {gap.strip()!r}"
        assignments.append({"name": item.group("name"), "value": item.group("value")})
        cursor = item.end()
    if body[cursor:].strip():
        return assignments, f"unsupported .param syntax near {body[cursor:].strip()!r}"
    if not assignments:
        return [], ".param line has no editable name=value assignment"
    return assignments, None


def parse_parameters(text: str) -> tuple[list[dict[str, str]], list[str]]:
    assignments: list[dict[str, str]] = []
    errors: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not PARAM_LINE_RE.match(line):
            continue
        parsed, error = parse_param_line(line)
        for item in parsed:
            item = dict(item)
            item["line"] = str(line_number)
            assignments.append(item)
        if error:
            errors.append(f"line {line_number}: {error}")
    return assignments, errors


def parameter_values(text: str) -> tuple[dict[str, dict[str, str]], list[str]]:
    assignments, errors = parse_parameters(text)
    values: dict[str, dict[str, str]] = {}
    for item in assignments:
        normalized = item["name"].lower()
        if normalized in values:
            errors.append(f"duplicate .param name cannot be edited deterministically: {item['name']}")
            continue
        values[normalized] = item
    return values, errors


def replace_parameters(text: str, params: dict[str, object]) -> str:
    if not params:
        return text
    _, errors = parse_parameters(text)
    if errors:
        raise ValueError("; ".join(errors))
    remaining = {str(key).lower(): (str(key), value) for key, value in params.items()}
    output: list[str] = []
    for line in text.splitlines():
        if not PARAM_LINE_RE.match(line):
            output.append(line)
            continue

        def replace_match(match: re.Match[str]) -> str:
            key = match.group("name").lower()
            if key not in remaining:
                return match.group(0)
            _, value = remaining.pop(key)
            return f"{match.group('name')}={format_value(value)}"

        output.append(PARAM_ASSIGN_RE.sub(replace_match, line))
    insert_at = next((index for index, line in enumerate(output) if line.strip().lower() == ".end"), len(output))
    output[insert_at:insert_at] = [f".param {key}={format_value(value)}" for key, value in remaining.values()]
    return "\n".join(output) + "\n"


def format_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return f"{float(value):.12g}"


def _dependency_target(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        return token[1:-1]
    if len(token) >= 2 and token[0] == "<" and token[-1] == ">":
        return token[1:-1]
    return token


def parse_dependency_line(line: str) -> dict[str, str] | None:
    match = DEPENDENCY_LINE_RE.match(line)
    if not match:
        return None
    return {
        "indent": match.group("indent"),
        "kind": match.group("kind").lower(),
        "token": match.group("target"),
        "target": _dependency_target(match.group("target")),
        "rest": match.group("rest"),
    }


def _path_key(path: Path) -> str:
    return str(path.resolve()).casefold()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _dependency_scope(requested: str, resolved: Path, root: Path) -> str:
    """Classify how a dependency is expected to be found.

    A path relative to the source NET is local.  An absolute path or an
    existing path outside that directory is external.  A bare unresolved
    token is allowed to come from LTspice's configured search path; its
    contents cannot be fingerprinted here.
    """

    requested_path = Path(requested).expanduser()
    if requested_path.is_absolute():
        return "external"
    if resolved.is_file():
        return "local" if _is_within(resolved, root) else "external"
    if len(requested_path.parts) == 1:
        return "search_path"
    return "local" if _is_within(resolved, root) else "external"


def dependency_manifest(net_path: Path, text: str) -> dict[str, Any]:
    """Collect dependencies and record whether their contents are verifiable.

    Local files are resolved relative to the NET.  Known files outside that
    directory are external dependencies.  Bare unresolved names are treated
    as LTspice search-path dependencies rather than falsely reported as local
    missing files.  Search-path and otherwise unreadable dependencies make
    evidence non-reusable because their content is not bound to the key.
    """

    records: list[dict[str, Any]] = []
    visited: set[str] = set()
    source_root = net_path.resolve().parent

    def visit(parent: Path, body: str, origin: str) -> None:
        for line_number, line in enumerate(body.splitlines(), start=1):
            parsed = parse_dependency_line(line)
            if parsed is None:
                continue
            requested = parsed["target"]
            candidate = Path(requested).expanduser()
            resolved = candidate if candidate.is_absolute() else parent / candidate
            resolved = resolved.resolve()
            scope = _dependency_scope(requested, resolved, source_root)
            exists = resolved.is_file()
            content_verified = False
            digest: str | None = None
            if exists:
                try:
                    digest = sha256_file(resolved)
                    content_verified = True
                except OSError:
                    content_verified = False
            item: dict[str, Any] = {
                "origin": origin,
                "line": line_number,
                "kind": parsed["kind"],
                "requested": requested,
                "resolved": str(resolved),
                "scope": scope,
                "classification": scope,
                "resolved_by": scope,
                "exists": exists,
                "sha256": digest,
                "content_verified": content_verified,
                "verified": content_verified,
            }
            records.append(item)
            key = _path_key(resolved)
            if not content_verified or key in visited:
                continue
            visited.add(key)
            try:
                child = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            visit(resolved.parent, child, str(resolved))

    visit(net_path.resolve().parent, text, str(net_path.resolve()))
    errors = [f"missing local dependency: {item['requested']} ({item['origin']}:{item['line']})"
              for item in records if not item["exists"] and item["scope"] == "local"]
    warnings = [
        f"unverified {item['scope']} dependency: {item['requested']} ({item['origin']}:{item['line']})"
        for item in records if not item["content_verified"] and item["scope"] != "local"
    ]
    unverified = [item["requested"] for item in records if not item["content_verified"]]
    return {
        "version": 2,
        "net": str(net_path.resolve()),
        "ok": not errors,
        "binding": "unverified" if unverified else "verified",
        "errors": errors,
        "warnings": warnings,
        "content_verified": not unverified,
        "reuse_allowed": not unverified,
        "unverified": unverified,
        "files": records,
    }


def _quote_dependency(path: str, original_token: str) -> str:
    if original_token.startswith("<") and original_token.endswith(">"):  # preserve LTspice library syntax
        return f"<{path}>"
    if original_token.startswith('"') or any(char.isspace() for char in path):
        return f'"{path}"'
    return path


def rewrite_dependency_text(
    text: str,
    source_parent: Path,
    destination_parent: Path,
    staged: dict[str, Path],
) -> str:
    output: list[str] = []
    for line in text.splitlines():
        parsed = parse_dependency_line(line)
        if parsed is None:
            output.append(line)
            continue
        requested = Path(parsed["target"]).expanduser()
        resolved = (requested if requested.is_absolute() else source_parent / requested).resolve()
        destination = staged.get(_path_key(resolved))
        if destination is None:
            output.append(line)
            continue
        relative = os.path.relpath(destination, destination_parent).replace(os.sep, "/")
        token = _quote_dependency(relative, parsed["token"])
        output.append(f"{parsed['indent']}.{parsed['kind']} {token}{parsed['rest']}")
    return "\n".join(output) + "\n"


def stage_net_with_dependencies(
    source_net: Path,
    rendered_text: str,
    job_dir: Path,
    manifest: dict[str, Any],
) -> Path:
    """Stage a derived NET and all recursive model files in a private folder."""

    if not manifest.get("ok"):
        raise ValueError("; ".join(str(item) for item in manifest.get("errors", [])))
    job_dir.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    unique: list[Path] = []
    for item in manifest.get("files", []):
        if not item.get("content_verified") or not item.get("exists"):
            continue
        source = Path(str(item["resolved"]))
        key = _path_key(source)
        if key in staged:
            continue
        unique.append(source)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name) or "dependency"
        staged[key] = job_dir / "deps" / f"{len(unique):03d}_{safe_name}"
    for source in unique:
        destination = staged[_path_key(source)]
        destination.parent.mkdir(parents=True, exist_ok=True)
        child = source.read_text(encoding="utf-8", errors="replace")
        rewritten = rewrite_dependency_text(child, source.parent, destination.parent, staged)
        destination.write_text(rewritten, encoding="utf-8")
    net_path = job_dir / source_net.name
    net_path.write_text(
        rewrite_dependency_text(rendered_text, source_net.resolve().parent, job_dir, staged),
        encoding="utf-8",
    )
    return net_path


def executable_fingerprint(executable: Path) -> dict[str, Any]:
    executable = executable.resolve()
    try:
        stat = executable.stat()
    except OSError:
        return {"path": str(executable), "exists": False}
    return {
        "path": str(executable),
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def simulation_evidence_payload(
    *,
    source_net_sha256: str,
    rendered_text: str,
    analysis: dict[str, object],
    params: dict[str, object],
    dependencies: dict[str, Any],
    executable: Path,
    ascii_output: bool = False,
) -> dict[str, Any]:
    """Return the simulation-only identity used for evidence reuse.

    The rendered analysis NET is the source-specific fingerprint.  The raw
    source NET hash is intentionally not part of the identity because
    render_analysis_net removes unrelated analysis directives before creating
    temporary jobs.  This lets a changed AC directive invalidate AC evidence
    without needlessly invalidating an unchanged DC job.
    """

    return {
        "evidence_version": 3,
        "rendered_net_sha256": sha256_text(rendered_text),
        "analysis": {
            "kind": str(analysis.get("kind", "")).lower(),
            "directive": str(analysis.get("directive", "")).strip(),
        },
        "parameters": {str(key).lower(): str(value) for key, value in sorted(params.items(), key=lambda item: str(item[0]).lower())},
        "dependencies": dependencies,
        "ltspice": executable_fingerprint(executable),
        "settings": {"ascii_output": bool(ascii_output), "flags": ["-b", "-Run"]},
    }


def simulation_evidence_key(
    *,
    source_net_sha256: str,
    rendered_text: str,
    analysis: dict[str, object],
    params: dict[str, object],
    dependencies: dict[str, Any],
    executable: Path,
    ascii_output: bool = False,
) -> str:
    return json_hash(simulation_evidence_payload(
        source_net_sha256=source_net_sha256,
        rendered_text=rendered_text,
        analysis=analysis,
        params=params,
        dependencies=dependencies,
        executable=executable,
        ascii_output=ascii_output,
    ))


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class EvidenceStore:
    """Hash-bound evidence records; metric specs are deliberately not stored in the key."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: dict[str, dict[str, Any]] = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("schema_version") == 1 and isinstance(data.get("records"), dict):
                    self.records = {str(key): value for key, value in data["records"].items() if isinstance(value, dict)}
            except (OSError, json.JSONDecodeError, AttributeError):
                self.records = {}

    def save(self) -> None:
        _atomic_write_json(self.path, {
            "schema_version": 1,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "records": self.records,
        })

    def _materialize(self, record: dict[str, Any], field: str, destination: Path) -> bool:
        source = Path(str(record.get(field, destination)))
        expected = str(record.get(f"{field}_sha256", ""))
        if not expected:
            return False
        candidates = [source, destination] if source.resolve() != destination.resolve() else [destination]
        valid_source: Path | None = None
        for candidate in candidates:
            if candidate.is_file() and candidate.stat().st_size > 0:
                try:
                    if expected and sha256_file(candidate) == expected:
                        valid_source = candidate
                        break
                except OSError:
                    continue
        if valid_source is None:
            return False
        if valid_source.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(valid_source, destination)
        return destination.is_file() and sha256_file(destination) == expected

    @staticmethod
    def _dependencies_are_reusable(simulation_input: dict[str, Any] | None) -> bool:
        if not isinstance(simulation_input, dict):
            return False
        dependencies = simulation_input.get("dependencies")
        if not isinstance(dependencies, dict):
            return False
        return bool(dependencies.get("reuse_allowed", True))

    def reuse(
        self,
        key: str,
        raw_destination: Path,
        log_destination: Path,
        *,
        simulation_input: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self._dependencies_are_reusable(simulation_input):
            return None
        record = self.records.get(key)
        if not isinstance(record, dict) or not record.get("simulation_ok"):
            return None
        if record.get("evidence_key") != key:
            return None
        recorded_input = record.get("simulation_input")
        if not self._dependencies_are_reusable(recorded_input if isinstance(recorded_input, dict) else None):
            return None
        if not record.get("fresh_raw") or not record.get("fresh_log"):
            return None
        if not self._materialize(record, "raw", raw_destination):
            return None
        if not self._materialize(record, "log", log_destination):
            return None
        try:
            errors = find_errors(log_destination.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        if errors:
            return None
        return {
            "ok": True,
            "reused": True,
            "raw": str(raw_destination),
            "log": str(log_destination),
            "errors": [],
            "returncode": 0,
            "fresh_raw": True,
            "fresh_log": True,
            "evidence_generated_at_utc": record.get("generated_at_utc"),
            "run_report": record.get("run_report"),
            "simulation_input": record.get("simulation_input"),
        }

    def record_success(
        self,
        key: str,
        *,
        raw: Path,
        log: Path,
        run_report: Path | None,
        result: dict[str, Any],
        simulation_input: dict[str, Any] | None = None,
    ) -> None:
        self.records[key] = {
            "evidence_key": key,
            "simulation_ok": True,
            "fresh_raw": bool(result.get("fresh_raw")),
            "fresh_log": bool(result.get("fresh_log")),
            "raw": str(raw.resolve()),
            "log": str(log.resolve()),
            "raw_sha256": sha256_file(raw),
            "log_sha256": sha256_file(log),
            "run_report": str(run_report.resolve()) if run_report and run_report.is_file() else None,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": result.get("elapsed_seconds"),
            "simulation_input": simulation_input or {},
        }
        self.save()
