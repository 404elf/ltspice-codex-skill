---
name: ltspice-sim-v2
description: Portable Codex Skill for generating, simulating, validating, measuring, and schematizing LTspice circuits with deterministic RAW/LOG checks and Weave NET-to-ASC verification.
---

# LTspice Codex Skill

## Purpose / setup

Use this Skill for LTspice circuit design, modification, simulation, measurement, and schematic generation. The `.net`/`.cir` is the electrical source of truth; never hand-author ASC coordinates. Keep legacy LTSPICE-AI and the old LTspice Skill out of the runtime path.

If `.ltspice-codex-config.json` is missing or invalid, automatically run `py -3 bootstrap.py` from the Skill repository root and use the resulting configuration. Stop only when a required external installation or permission is unavailable. Keep each circuit's current NET, RAW/LOG, optional ASC, Weave result, plots, and summary together.

## Fast Path

For LTspice circuit design, simulation, modification, or validation, use this Skill immediately.

- Trust an existing valid local configuration.
- `AUTO`: simple/passive → `QUICK`; ordinary analog/tolerance → `STANDARD`; switching/power/strong nonlinear/high-risk feedback → `STRICT`; repeated variants → `BATCH`.
- Plan topology, values, analyses, metrics, and tolerance strategy once.
- Run one deterministic validation entrypoint for the complete plan.
- Read compact PASS/FAIL first.
- On engineering failure, diagnose only the failed requirement.
- After electrical PASS, run Weave once.
- `STRICT`: run one final ASC smoke validation.
- Stop when proven.

If this Skill is selected and local configuration is valid, do not inspect README, bootstrap, helper source, repository history, installation instructions, or troubleshooting documentation before normal circuit work. Read them only for missing/invalid configuration, explicit setup, infrastructure/helper failure, unexpected behavior, or a user request for implementation details.

## Modes

`QUICK`, `STANDARD`, `STRICT`, and `BATCH` are final validation plans, not an execution ladder. `STANDARD`/`STRICT` normally call the validation suite once; do not run a duplicate nominal QUICK first. `accuracy-sensitive` alone does not imply `STRICT`.

- `QUICK`: sufficient nominal validation, fresh RAW/LOG, clean LOG, and Weave `MATCH`.
- `STANDARD`: one nominal/tolerance plan and Weave `MATCH`.
- `STRICT`: one complete high-risk plan, Weave `MATCH`, and one final LTspice smoke run of the ASC.
- `BATCH`: validate variants together; generate ASC only for selected/final candidates.

## Execution policy

Plan once. The Agent owns topology, values, trade-offs, requirements, relevant analyses, and genuine design diagnosis. The deterministic layer owns intent normalization, paths/config, schema, dependencies, corners, RAW/LOG, metrics, evidence, and command assembly.

Use only this normal-path entrypoint; do not manually build the internal suite spec or concatenate executable/helper/output/config paths:

```powershell
<configured-python> scripts/run_validation_intent.py `
  --net <final.net> `
  --intent <validation-intent.json>
```

Intent fields are only `mode`, optional `analyses`, `requirements`, `tolerances`, and `required_nets`. An analysis entry is normally `{}` to use the matching directive in the final NET; an explicit `directive` is the only optional analysis detail. Use this shape:

```json
{"mode":"STANDARD","analyses":{"ac":{}},"requirements":[
  {"name":"cutoff","analysis":"ac","measure":"cutoff","signal":"V(out)","reference":"V(in)","target":1000,"tolerance":10}
]}
```

The entrypoint fills safe defaults, canonicalizes representation-only differences, writes the internal spec beside the circuit output, and delegates to `run_validation_suite.py`. It never deletes requirements, loosens targets, omits requested corners, or turns FAIL into PASS. Prefer one analysis for multiple requirements; add analyses/corners only when existing evidence cannot prove the requirement. Reduce corners only when endpoint worst-case directions are clear; otherwise let the suite decide. Reuse only successful evidence bound to current simulation inputs; metric/target/trace/report changes may reparse it, while changed NET/analysis/parameter/dependency/executable/settings invalidate affected evidence.

## Required workflow

1. Generate or update the final NET with ground `0`, unique references, required analyses, and `.end`; ordinary parameter changes update the existing NET.
2. Choose the final mode, define one intent, and call the entrypoint once. The suite performs dry-run, dependency/preflight, fresh RAW/LOG, metrics, corners, and evidence handling.
3. Read compact PASS/FAIL first. Do not reopen proven RAW/LOG or recalculate passing metrics; diagnose only failed requirements and rerun only invalidated work.
4. After the exact final NET passes, run Weave once on that NET and accept the ASC only with round-trip `MATCH`; never edit coordinates manually.
5. In `STRICT`, run the generated ASC once with LTspice as a final parse/model/directive/startup smoke check, not a second engineering suite.

## Failure handling

Exit code 0 alone is never simulation success. Do not report success without the final NET validation, newly-created RAW/LOG, clean LOG, required metrics/corners, Weave `MATCH`, and—when `STRICT`—successful ASC smoke. Old or unverified artifacts never satisfy a current run; stop after required gates pass.

## Finalization / artifacts

For ordinary parameter changes, rerun the updated NET and replace current RAW/LOG, then replace the ASC regenerated by Weave from that exact NET. NET and ASC must describe the same current circuit. Preserve history only when explicitly requested; in `BATCH`, generate ASC only for selected/final candidates.

After every successful run, report concisely:

- output directory
- final `.net` path
- final `.asc` path, if generated
- final `.raw` path
- final `.log` path
- Weave verification result path, if generated

Also report requested measurements and validation status. `MATCH` proves connectivity equivalence, not electrical correctness or target compliance.

## Helper commands

Normal validation uses only `scripts/run_validation_intent.py`. Inspect lower-level helpers only for an entrypoint-reported infrastructure/helper failure or explicit troubleshooting. Weave remains the only NET-to-ASC converter.
