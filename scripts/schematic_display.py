"""Prepare passive-value parameters before the canonical NET is simulated."""

from __future__ import annotations

import re


MARKER = "* @ltspice-skill display=compact hide="
SCALAR = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[A-Za-z]*\Z")
REFERENCE = re.compile(r"\{[A-Za-z_]\w*\}\Z")


def prepare_schematic_display(text: str, mode: str = "auto") -> str:
    """Parameterize simple top-level R/L/C cards; preserve tokens and scopes.

    Auto is deliberately conservative: eight eligible passives and at least
    four value strings longer than eight characters. Explicit compact/inline
    overrides the heuristic. Expressions, device options, and subcircuits are
    left alone. Each numeric component gets its own collision-free parameter.
    """
    if mode not in {"auto", "compact", "inline"}:
        raise ValueError("schematic_display must be auto, compact, or inline")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    marked = any(line.startswith(MARKER) for line in lines)
    if mode == "inline":
        return "".join(line for line in lines if not line.startswith(MARKER))

    cards = []
    depth = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"\.subckt\b", stripped, re.I):
            depth += 1
        elif re.match(r"\.ends\b", stripped, re.I):
            depth = max(0, depth - 1)
        if depth or (index + 1 < len(lines) and lines[index + 1].lstrip().startswith("+")):
            continue
        match = re.fullmatch(r"(\s*([RLC][\w.-]*)\s+\S+\s+\S+\s+)([^;\s]+)(\s*(?:;[^\r\n]*)?)(?:\r?\n)?", line, re.I)
        if match and (SCALAR.fullmatch(match[3]) or REFERENCE.fullmatch(match[3])):
            cards.append((index, match))
    if marked:
        previous = next(line[len(MARKER):].strip().split(",") for line in lines if line.startswith(MARKER))
        if previous == [match[2] for _, match in cards] and not any(SCALAR.fullmatch(match[3]) for _, match in cards):
            return text
        # A subsequent circuit edit may introduce a numeric card. Refresh the
        # display marker without discarding existing parameter definitions.
        unmarked = "".join(line for line in lines if not line.startswith(MARKER)
                           and line.strip() != "* Parameters (component values)")
        return prepare_schematic_display(unmarked, "compact")
    if not cards or (mode == "auto" and (len(cards) < 8 or sum(len(m[3]) > 8 for _, m in cards) < 4)):
        return text

    reserved = {token.casefold() for token in re.findall(r"[A-Za-z_]\w*", text)}
    parameters: dict[str, list[str]] = {kind: [] for kind in "LCR"}
    names = []
    for index, match in cards:
        name, value = match[2], match[3]
        names.append(name)
        if not SCALAR.fullmatch(value):
            continue  # Existing parameter references retain their definitions.
        base = re.sub(r"\W", "_", name) + "v"
        parameter = base
        suffix = 2
        while parameter.casefold() in reserved:
            parameter = f"{base}_{suffix}"
            suffix += 1
        reserved.add(parameter.casefold())
        parameters[name[0].upper()].append(f"{parameter}={value}")
        ending = newline if lines[index].endswith("\n") else ""
        lines[index] = match[1] + "{" + parameter + "}" + match[4].rstrip("\r\n") + ending

    block = [MARKER + ",".join(names), "* Parameters (component values)"]
    for assignments in parameters.values():
        row = ".param"
        count = 0
        for assignment in assignments:
            if count and (len(row) + len(assignment) + 1 > 88 or count == 3):
                block.append(row)
                row, count = ".param", 0
            row += " " + assignment
            count += 1
        if count:
            block.append(row)
    insertion = next((i for i, line in enumerate(lines) if re.match(r"\s*\.end\s*(?:;.*)?$", line, re.I)), len(lines))
    if insertion and not lines[insertion - 1].endswith("\n"):
        lines[insertion - 1] += newline
    lines[insertion:insertion] = [newline.join(block) + newline]
    return "".join(lines)
