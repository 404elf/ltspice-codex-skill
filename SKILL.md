---
name: ltspice-sim-v2
description: Portable Codex Skill for generating, simulating, validating, measuring, and schematizing LTspice circuits with deterministic RAW/LOG checks and Weave NET-to-ASC verification.
---

# LTspice Codex Skill

## Purpose / setup

Use this skill to create or modify the exact LTspice `.net`/`.cir`, run the configured LTspice, measure results, and optionally create a Weave schematic. Keep legacy LTSPICE-AI and the old LTspice skill out of the runtime path. Never use another LLM, Streamlit, or a manually drawn ASC.

If `.ltspice-codex-config.json` is missing or invalid, automatically run `py -3 bootstrap.py` from the skill repository root, then read the generated configuration. Stop only when setup is blocked by a missing external installation or permission. Use configured paths; do not hardcode machine-specific paths in circuit artifacts or this skill.

Use one output folder per circuit below the configured output root. Keep the exact NET, current RAW/LOG, optional ASC, Weave result, plots, and summary together.

## Modes

- `AUTO`: choose the lightest sufficient final plan and escalate only after a failed gate, suspicious topology, or inconsistent result.
- `QUICK`: run the exact NET with fresh RAW/LOG and an error-free LOG, then convert it with Weave and require `MATCH`; do not run the ASC.
- `STANDARD`: one complete nominal/tolerance plan through the validation suite, plus Weave `MATCH`.
- `STRICT`: one complete plan with high-risk topology checks, saturation checks, Weave `MATCH`, and one final LTspice smoke run of the generated ASC.
- `BATCH`: validate variants in one suite; generate ASC only for selected/final candidates.

`QUICK`, `STANDARD`, `STRICT`, and `BATCH` are final plans, not a ladder. Do not run a duplicate nominal QUICK before a STANDARD or STRICT suite that already covers the required analyses. `accuracy-sensitive` alone does not force STRICT.

## Execution policy

- Plan once before tools: topology, values, analyses, metrics, tolerance strategy, and required gates.
- Prefer one validation-suite call. The suite owns spec dry-run, dependency handling, fresh RAW/LOG checks, metrics, corners, and evidence reuse.
- Read the compact suite summary or `agent_summary` first. Do not reopen proven RAW/LOG files or recalculate passing metrics unless the summary is missing, contradictory, or failed. Use `--verbose-json` only when full stdout JSON is explicitly needed.
- Let one analysis prove as many requirements as its data supports. Add an analysis or corner only when the existing evidence cannot prove the requirement.
- Reduce corners only when endpoint worst-case directions are clear; otherwise let the deterministic suite handle the corner plan without per-corner reasoning.
- Reuse only successful evidence bound to the current simulation inputs. Metric/target/trace/report changes may reparse matching evidence; changed NET, analysis, parameters, dependencies, executable, or run settings invalidate affected evidence.
- A simulation-level failure stops dependent analyses/corners. A metric failure does not invalidate successful independent simulations. Diagnose and rerun only invalidated work.
- Stop when the requested engineering results and required gates pass. Do not add optional checks after PASS.

## Required workflow

1. Generate or update the NET with explicit ground `0`, unique references, required analyses, and `.end`. The NET is the electrical source of truth; never hand-author ASC coordinates.
2. For `STANDARD` or `STRICT`, write the validation spec and call `scripts/run_validation_suite.py` once for the final plan. For `QUICK`, use the direct LTspice helper when a suite is unnecessary.
3. Count a simulation only when the helper/suite confirms newly-created RAW and LOG files and a clean LOG. Exit code 0 alone is not success; parser, fatal, unresolved-model, singular-matrix, or aborted-simulation errors fail the run. Old artifacts never satisfy the current run.
4. Read the compact result and inspect only failed or contradictory evidence. Do not treat an unverified or stale artifact as current.
5. After the final NET passes required LTspice validation, call Weave once on that exact NET. Accept the ASC only when round-trip verification returns `MATCH`; never edit its coordinates manually.
6. In `STRICT`, run the generated ASC once with LTspice. This is a final parse/startup validation, not a second engineering suite.

## Failure handling

Do not report success if the final NET, fresh RAW/LOG, clean LOG, required metrics, Weave `MATCH`, or STRICT ASC smoke gate fails. Preserve the source NET and revise only the failed design or metric condition. Do not restart already-proven independent analyses.

## Finalization / artifacts

For ordinary parameter-only changes, update the existing NET, rerun LTspice and replace the current RAW/LOG, then regenerate and replace the ASC from that exact NET with Weave. NET and ASC must describe the same current state. Do not create versioned copies unless history preservation is explicitly requested.

After every successful run, report concisely:

- output directory
- final `.net` path
- final `.asc` path, if generated
- final `.raw` path
- final `.log` path
- Weave verification result path, if generated

Also report requested measurements and validation status. `MATCH` proves connectivity equivalence only; it does not prove electrical correctness or target compliance.

## Helper commands

Use the Python executable and paths from `.ltspice-codex-config.json`:

```powershell
python scripts/run_ltspice.py --input <net-or-asc> --ltspice <configured-executable> [--ascii]
python scripts/run_validation_suite.py --net <net> --spec <validation-spec.json> --ltspice <configured-executable>
python scripts/weave_convert.py --net <exact-net> --weave-dir <configured-weave-cli> --asc <asc> --force
python scripts/parse_raw.py --raw <raw> --trace <name>
python scripts/validate_log.py --log <log>
python scripts/preflight.py --net <net> --required-net <name>
```

Weave owns NET-to-ASC conversion. Use `--ascii` only for text diagnostics; binary RAW is the normal path.
