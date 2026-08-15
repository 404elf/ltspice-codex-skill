---
name: ltspice-sim-v2
description: Portable Codex Skill for generating, simulating, validating, measuring, and schematizing LTspice circuits with deterministic RAW/LOG checks and Weave NET-to-ASC verification. Use when Codex needs to design or modify LTspice circuits after this repository is installed on a machine with LTspice.
---

# LTspice Codex Skill

Use this skill for LTspice circuit work. Codex is the AI layer: generate or modify the exact SPICE netlist, run the configured LTspice executable, validate fresh RAW/LOG outputs, optionally parse RAW data, and use the configured Weave CLI for requested schematics. Do not use Streamlit, OpenRouter, another LLM API, or any legacy LTspice automation project.

Read the machine configuration written by `bootstrap.py` before running tools. It contains the detected LTspice executable, Weave CLI directory, Python executable, and output root. Never hardcode machine-specific paths in a circuit artifact or this skill.

Use one output folder per circuit below the configured output root. Keep the exact `.net`, fresh `.raw/.log`, optional `.asc`, Weave result, plots, and summary together.

## First-time setup

When this Skill is being installed or its local configuration file is missing or invalid, run `py -3 bootstrap.py` from the Skill repository root before doing circuit work. The bootstrap script detects LTspice and Node.js, creates the isolated Python environment, obtains Weave and its npm dependency, writes `.ltspice-codex-config.json`, and runs the RC smoke test. Then read that configuration and use its paths. Do not ask the user to manually assemble Python or Weave dependencies unless setup is blocked by a missing external installation or permission.

## Modes

- `AUTO` is the default. Choose the lightest sufficient mode and escalate only after a failure, missing RAW, LOG error, inconsistent result, Weave failure, or suspicious topology.
- `QUICK`: passive/trivial circuits, parameter-only changes, or validated topology. Run the exact NET, require fresh RAW/LOG and an error-free LOG, convert the exact NET, and require Weave `MATCH`; do not run the ASC.
- `STANDARD`: ordinary new analog circuits. Do QUICK plus a lightweight topology check, the requested engineering measurement, and Weave `MATCH`.
- `STRICT`: multistage, feedback-sensitive, switching, power, accuracy-sensitive, or explicitly fully validated circuits. Do STANDARD plus checks for floating inputs, rails, shorts/opens, feedback polarity, impossible operating conditions, appropriate analyses, saturation, and one final LTspice run of the generated ASC.
- `BATCH`: repeated variants or sweeps. Avoid repeated topology audits and ASC generation for every variant; parse requested outputs and deeply validate failures, outliers, and final candidates.

## Required workflow

1. Generate or modify the `.net`/`.cir` file with explicit ground node `0`, unique reference designators, an analysis directive, and `.end`. Do not manually author ASC coordinates.
2. Before every run, use `scripts/run_ltspice.py`; it archives stale same-stem RAW/LOG files, runs LTspice, requires newly-created RAW and LOG files, and validates the fresh LOG. RAW is binary by default; use `--ascii` only for text diagnostics. When the input is an ASC, the helper stages it in a temporary directory so LTspice cannot overwrite the source NET; the additional validation artifacts use an `-asc` stem.
3. Do not treat LTspice exit code 0 alone as success. Reject unresolved parser/simulation failures such as `Error`, `Fatal`, `No such`, `Unknown`, `Singular matrix`, `Voltage not found`, and aborted-simulation messages. A direct-Newton fallback is acceptable only when the LOG confirms successful Gmin operating-point recovery.
4. For STANDARD and STRICT, after design and preflight are stable, write a small JSON validation specification and run `scripts/run_validation_suite.py` once. It executes requested analyses and deterministic corners/sweeps, validates fresh RAW/LOG files, extracts only requested traces, evaluates metrics, records wall-clock timings, and writes one `validation_summary.json`. Do not ask the model to inspect each corner or RAW file individually.
5. Read the suite summary. If it fails, revise only the failed design or metric checks and rerun the suite. The suite cache may reuse unchanged preflight state only; it must never treat an old RAW/LOG as a fresh simulation.
6. After the final NET passes, and only then, pass that exact NET to `scripts/weave_convert.py`. It fingerprints the NET before and after conversion, refuses a changed NET, runs round-trip verification, and accepts only `MATCH`. Do not run Weave for intermediate parameter or corner variants.
7. For STRICT, run the generated ASC with `scripts/run_ltspice.py` as an additional final validation. The final result is successful only when the suite, Weave `MATCH`, and ASC validation pass.
8. Use `scripts/parse_raw.py` with the configured Python executable when numerical RAW parsing is needed outside the suite. Use plots only when they materially help the requested result.

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

- `scripts/run_ltspice.py --input <net-or-asc> --ltspice <configured-executable> [--ascii]`
- `scripts/run_validation_suite.py --net <net> --spec <validation-spec.json> --ltspice <configured-executable> [--markdown <summary.md>]`
- `scripts/validate_log.py --log <log>`
- `scripts/parse_raw.py --raw <raw> [--trace <name>]`
- `scripts/weave_convert.py --net <exact-net> --weave-dir <configured-weave-cli> --asc <asc> --force`
- `scripts/preflight.py --net <net> [--required-net <name>]`

Weave owns NET-to-ASC conversion. Never guess or hand-edit ASC symbols, wires, or coordinates.
