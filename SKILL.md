---
name: ltspice-sim-v2
description: Portable Codex Skill for generating, simulating, validating, measuring, and schematizing LTspice circuits with deterministic RAW/LOG checks and Weave NET-to-ASC verification. Use when Codex needs to design or modify LTspice circuits after this repository is installed on a machine with LTspice.
---

# LTspice Codex Skill

Use this skill for LTspice circuit work. Codex is the AI layer: generate or modify the exact SPICE netlist, run the configured LTspice executable, validate fresh RAW/LOG outputs, optionally parse RAW data, and use the configured Weave CLI for requested schematics. Do not use Streamlit, OpenRouter, another LLM API, or any legacy LTspice automation project.

Read the machine configuration written by `bootstrap.py` before running tools. It contains the detected LTspice executable, Weave CLI directory, Python executable, and output root. Never hardcode machine-specific paths in a circuit artifact or this skill.

Use one output folder per circuit below the configured output root. Keep the exact `.net`, fresh `.raw/.log`, optional `.asc`, Weave result, plots, and summary together.

## Modes

- `AUTO` is the default. Choose the lightest sufficient mode and escalate only after a failure, missing RAW, LOG error, inconsistent result, Weave failure, or suspicious topology.
- `QUICK`: passive/trivial circuits, parameter-only changes, or validated topology. Run the exact NET, require fresh RAW/LOG and an error-free LOG, convert the exact NET, and require Weave `MATCH`; do not run the ASC.
- `STANDARD`: ordinary new analog circuits. Do QUICK plus a lightweight topology check, the requested engineering measurement, and Weave `MATCH`.
- `STRICT`: multistage, feedback-sensitive, switching, power, accuracy-sensitive, or explicitly fully validated circuits. Do STANDARD plus checks for floating inputs, rails, shorts/opens, feedback polarity, impossible operating conditions, appropriate analyses, saturation, and one final LTspice run of the generated ASC.
- `BATCH`: repeated variants or sweeps. Avoid repeated topology audits and ASC generation for every variant; parse requested outputs and deeply validate failures, outliers, and final candidates.

## Required workflow

1. Generate or modify the `.net`/`.cir` file with explicit ground node `0`, unique reference designators, an analysis directive, and `.end`. Do not manually author ASC coordinates.
2. Before every run, use `scripts/run_ltspice.py`; it archives stale same-stem RAW/LOG files, runs LTspice, requires newly-created RAW and LOG files, and validates the fresh LOG.
3. Do not treat LTspice exit code 0 alone as success. Reject unresolved parser/simulation failures such as `Error`, `Fatal`, `No such`, `Unknown`, `Singular matrix`, `Voltage not found`, and aborted-simulation messages. A direct-Newton fallback is acceptable only when the LOG confirms successful Gmin operating-point recovery.
4. For normal circuit runs, pass the exact successfully simulated NET to `scripts/weave_convert.py`. It fingerprints the NET before and after conversion, refuses a changed NET, runs round-trip verification, and accepts only `MATCH`.
5. For STRICT, run the generated ASC with `scripts/run_ltspice.py` as an additional final validation. The final result is successful only when NET validation, Weave `MATCH`, and ASC validation pass.
6. Use `scripts/parse_raw.py` with the configured Python executable when numerical RAW parsing is needed. Use plots only when they materially help the requested result.

## Artifact policies

`.net` is the source of truth. For ordinary parameter-only modifications, update the existing NET, rerun LTspice and replace the current RAW/LOG, then regenerate and replace the ASC from that exact updated NET using Weave so NET and ASC represent the same current state. Do not create versioned copies unless history preservation is explicitly requested. In `BATCH`, generate ASC only for selected/final candidates.

After every successful run, always report this concise list:

- Output directory: `<folder>`
- Final `.net`: `<path>`
- Final `.asc`: `<path>` (if generated)
- Final `.raw`: `<path>`
- Final `.log`: `<path>`
- Weave verification result: `<path>` (if generated)

Also report requested measurements and relevant validation status. Do not claim success when a required gate fails. `MATCH` proves connectivity equivalence only; it does not prove SPICE syntax, electrical correctness, or engineering-target compliance.

## Helpers

Run these using the configured Python executable:

- `scripts/run_ltspice.py --input <net-or-asc> --ltspice <configured-executable>`
- `scripts/validate_log.py --log <log>`
- `scripts/parse_raw.py --raw <raw> [--trace <name>]`
- `scripts/weave_convert.py --net <exact-net> --weave-dir <configured-weave-cli> --asc <asc> --force`
- `scripts/preflight.py --net <net> [--required-net <name>]`

Weave owns NET-to-ASC conversion. Never guess or hand-edit ASC symbols, wires, or coordinates.
