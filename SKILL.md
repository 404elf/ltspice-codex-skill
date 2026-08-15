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
- `STANDARD`: ordinary new analog circuits and ordinary tolerance analysis. Run its complete requested plan once, with topology checks, engineering measurements, and Weave `MATCH`.
- `STRICT`: multistage, feedback-sensitive, switching, power, strongly nonlinear, high-risk, abnormal-result, or explicitly fully validated circuits. Run its complete requested plan once, including floating-input/rail/short/open/feedback checks, saturation checks, Weave `MATCH`, and one final LTspice run of the generated ASC.
- `BATCH`: repeated variants or sweeps. Avoid repeated topology audits and ASC generation for every variant; parse requested outputs and deeply validate failures, outliers, and final candidates.

These are final validation plans, not a `QUICK` -> `STANDARD` -> `STRICT` ladder. Do not run a duplicate nominal QUICK before a STANDARD or STRICT suite that already covers the nominal analyses. `accuracy-sensitive` alone does not force STRICT; choose STANDARD with tolerance analysis for a normal linear circuit unless the topology or request warrants STRICT.

## Required workflow

1. Generate or modify the `.net`/`.cir` file with explicit ground node `0`, unique reference designators, an analysis directive, and `.end`. Do not manually author ASC coordinates.
2. Before any formal validation suite, run its pure static validation-spec dry-run. It must reject invalid schema, missing or conflicting analysis directives, `.op` axis metrics, invalid `.dc start=stop`, missing traces/references, unknown corner parameters, unsupported `.param` syntax, and unsafe corner plans before LTspice is called. Use `--dry-run` to inspect the result without running LTspice.
3. Separate simulation evidence from metric evaluation. A successful fresh RAW/LOG is keyed by the exact source/rendered NET, analysis directive, parameters, recursive `.lib`/`.include` content, LTspice executable fingerprint, and run settings. Store this in `simulation_evidence.json`. A metric, target, tolerance, trace selection, or summary-format change must reuse only a matching hash-bound successful evidence record and reparse the RAW; it must not invoke LTspice. Never treat an unbound or failed RAW/LOG as evidence.
4. Before every new LTspice run, use `scripts/run_ltspice.py`; it archives stale same-stem RAW/LOG files, runs LTspice, requires newly-created RAW and LOG files, and validates the fresh LOG. RAW is binary by default; use `--ascii` only for text diagnostics. When the input is an ASC, the helper stages it in a temporary directory so LTspice cannot overwrite the source NET; the additional validation artifacts use an `-asc` stem.
5. Do not treat LTspice exit code 0 alone as success. Reject unresolved parser/simulation failures such as `Error`, `Fatal`, `No such`, `Unknown`, `Singular matrix`, `Voltage not found`, and aborted-simulation messages. A direct-Newton fallback is acceptable only when the LOG confirms successful Gmin operating-point recovery.
6. For STANDARD and STRICT, after the dry-run passes, run `scripts/run_validation_suite.py` once for the complete final plan. It executes requested analyses and corners, validates fresh RAW/LOG files, extracts only requested traces, evaluates metrics, records timing, and writes one `validation_summary.json`. One analysis should prove as many requirements as its RAW permits; do not create one LTspice job per metric.
7. Use mathematically justified corner reduction when possible. With `"corner_strategy": "monotonic"` and explicit `"monotonic"` directions, the suite runs only the proven endpoint extremes. Use Cartesian corners for nonlinear/coupled cases or when explicitly requested. A failed metric invalidates only the evidence it depends on; unchanged analyses remain reusable.
8. Derived analysis NETs preserve recursive `.lib`/`.include` dependencies by staging them with usable relative paths. Validation-only convergence hints are injected only into a temporary retry NET after a finite timeout; do not add UIC by default or pollute the source-of-truth NET.
9. After the final NET passes, and only then, pass that exact NET to `scripts/weave_convert.py`. It fingerprints the NET before and after conversion, refuses a changed NET, runs round-trip verification, and accepts only `MATCH`. Do not run Weave for intermediate parameter or corner variants.
10. For STRICT, run the generated ASC with `scripts/run_ltspice.py` as an additional final validation. This confirms ASC parsing/model/directive startup; it does not require rerunning the complete engineering suite. The final result is successful only when the suite, Weave `MATCH`, and ASC validation pass.
11. Use `scripts/parse_raw.py` with the configured Python executable when numerical RAW parsing is needed outside the suite. Use plots only when they materially help the requested result.

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

The validation suite keeps a small preflight cache keyed only by the NET hash, required nets, and preflight version. Metric/spec changes therefore do not invalidate topology preflight. Its summary reports actual LTspice calls and reused evidence so performance changes remain auditable.

## Helpers

Run these using the configured Python executable:

- `scripts/run_ltspice.py --input <net-or-asc> --ltspice <configured-executable> [--ascii]`
- `scripts/run_validation_suite.py --net <net> --spec <validation-spec.json> --ltspice <configured-executable> [--markdown <summary.md>]`
- `scripts/validate_log.py --log <log>`
- `scripts/parse_raw.py --raw <raw> [--trace <name>]`
- `scripts/weave_convert.py --net <exact-net> --weave-dir <configured-weave-cli> --asc <asc> --force`
- `scripts/preflight.py --net <net> [--required-net <name>]`

Weave owns NET-to-ASC conversion. Never guess or hand-edit ASC symbols, wires, or coordinates.
