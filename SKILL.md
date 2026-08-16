---
name: ltspice-sim-v2
description: Independent LTspice circuit generation, simulation, measurement, RAW/LOG validation, and Weave NET-to-ASC verification without the legacy LTSPICE-AI project.
---

# LTspice Simulation v2

## Fast Path

For LTspice circuit design, simulation, modification, or validation, use this Skill immediately.

- Trust an existing valid local configuration.
- AUTO: simple/passive → QUICK; ordinary analog/tolerance → STANDARD; switching/power/strong nonlinear/high-risk feedback → STRICT; repeated variants → BATCH.
- Plan topology, values, analyses, metrics, and tolerance strategy once.
- Run one deterministic validation-intent entrypoint for the complete plan.
- Read the compact PASS/FAIL result first.
- On engineering failure, diagnose only the failed requirement.
- After electrical PASS, run Weave once.
- In STRICT, run one final generated-ASC LTspice smoke validation.
- Stop when proven.

## Purpose and setup

The `.net`/`.cir` is the source of truth. The Skill owns the engineering design; deterministic helpers own simulation proof and artifact bookkeeping. Do not read, reuse, import, or depend on the legacy `ltspice-circuit-simulator` Skill or LTSPICE-AI.

The local configuration is `.ltspice-codex-config.json` beside this file. If it is missing or invalid, automatically run the available bootstrap/setup path and re-check it. When configuration is valid, do not inspect README files, bootstrap code, helper source, repository history, or troubleshooting notes before normal circuit work. Read those only for setup requests, infrastructure/helper failures, unexpected behavior, or implementation questions.

The installed configuration resolves Python, LTspice, the output root, and Weave. Do not make the Agent assemble those paths or executable arguments.

## Modes

`QUICK`, `STANDARD`, `STRICT`, and `BATCH` are final validation plans, not a QUICK → STANDARD → STRICT ladder. Choose the lightest plan that proves the request. `AUTO` selects the plan. Do not run a duplicate nominal QUICK before a STANDARD/STRICT suite unless it has clear engineering value.

## Execution policy

The Agent decides topology, component values, engineering requirements, relevant analyses, metrics, and genuine design diagnosis. Code handles normalization, paths, schema checks, simulation, RAW/LOG parsing, metric arithmetic, corner expansion, dependency/evidence bookkeeping, and summaries.

Use the thin deterministic entrypoint:

```text
<configured Python> scripts/run_validation_intent.py --net <final.net> --intent <validation-intent>
```

The intent is a small engineering-facing object containing only `mode`, `analyses`, `requirements`, `tolerances`, optional `required_nets`, and optional `model_policy`. Requirements may be top-level or nested under an analysis; tolerances may be global or grouped by analysis, and the adapter canonicalizes them into one suite plan. Set `model_policy: real_device_required` when concrete devices are required; known generic placeholders such as `UniversalOpAmp2` are rejected before LTspice. The interface must tolerate mechanically recoverable representation errors (for example comments, trailing commas, safe JSON-like literals, unambiguous aliases, and relative paths); it must not merely replace one fragile Agent-authored JSON schema with another. It must never guess or change engineering meaning.

The entrypoint is intentionally thin: intent normalization and validation → configuration/path resolution → canonical existing-suite spec → `run_validation_suite.py` → compact result. It must not implement a second validation system or duplicate LTspice execution, RAW/LOG validation, metric evaluation, corner logic, convergence policy, evidence/cache logic, dependency fingerprinting, or Weave logic.

Normal Agent-facing output is compact: `PASS`/`FAIL`, failure class, failed requirement, summary path, LTspice call count, and evidence-reuse count. Full details remain in the existing summary artifact.

## Required workflow

1. Create or update the exact final NET with explicit ground `0`, unique references, required analyses, and `.end`. Do not hand-author ASC coordinates.
2. Plan once. Prefer one analysis that proves several requirements; add an analysis or corner only when existing evidence cannot prove the requirement. Use mathematically justified worst-case endpoint reduction when clear; otherwise let the deterministic suite decide.
3. Call `run_validation_intent.py` once for the complete plan. It must finish representation, schema, path, and default handling before LTspice is called.
4. Read the compact result first, then the summary only as needed. Do not reopen proven RAW/LOG files or re-reason per corner.
5. A failed gate is either `PLUMBING/INFRASTRUCTURE FAILURE` or `ENGINEERING FAILURE`. Fix mechanical issues deterministically; re-enter the Agent only for an engineering decision. After failure, diagnose only the affected requirement/evidence.
6. Do not treat an old artifact as current evidence. Required runs must have fresh RAW and LOG files, and parser/fatal/simulation errors are failures even when LTspice exits with code 0.
7. Only after the final NET passes required electrical validation, run Weave once on that exact NET. Accept the ASC only when round-trip verification is `MATCH`.
8. In STRICT, run the generated ASC once with LTspice as the final smoke validation. Do not repeat the full engineering suite for this purpose.

## Failure handling

Do not silently delete requirements, loosen targets/tolerances, omit explicit corners, or turn a failure into a pass. Do not manually repair ASC coordinates. If the deterministic entrypoint reports an unexplainable infrastructure error, inspect setup/helper details; otherwise keep the next Agent invocation focused on the failed engineering requirement.

If model invocation count is not directly observable, do not infer or fabricate it. Record observable Agent stages, deterministic tool calls, tool-result → new-action transitions, and the cause as `startup`, `plumbing`, `engineering`, or `finalization`.

Do not invent the final blind engineering benchmark. After implementation and plumbing/startup tests, stop with `READY FOR EXTERNAL BLIND BENCHMARK`; the unseen circuit is supplied separately.

## Finalization and artifacts

The artifact update policy is strict: ordinary parameter-only changes update the existing NET, replace the current RAW/LOG after re-simulation, and regenerate the ASC from that exact NET with Weave. NET and ASC must represent the same current circuit state. Do not make versioned copies unless history preservation is requested. In BATCH, generate ASC only for selected/final candidates.

After every successful run, report concisely:

- output directory
- final `.net` path
- final `.asc` path, if generated
- final `.raw` path
- final `.log` path
- Weave verification result path, if generated

Report requested measurements and gate results as well. `MATCH` proves connectivity equivalence; it does not replace LTspice or engineering validation. Once the user requirements and required gates pass, stop.

## Helper commands

Use the configured Skill Python and paths. These are troubleshooting/reference commands; normal circuit work uses the intent entrypoint above.

```powershell
& '<configured Python>' '<skill>\scripts\run_ltspice.py' --input '<net-or-asc>' --ltspice '<LTspice.exe>'
& '<configured Python>' '<skill>\scripts\run_validation_suite.py' --net '<net>' --spec '<canonical-spec>' --ltspice '<LTspice.exe>'
& '<configured Python>' '<skill>\scripts\parse_raw.py' --raw '<raw>' --trace '<name>'
& '<configured Python>' '<skill>\scripts\weave_convert.py' --net '<exact-net>' --weave-dir '<Weave CLI>' --node '<node>' --asc '<asc>' --result '<verification>' --force
```

The public interface change is an adapter, not a second validator. When reporting implementation work, report changed files, diffstat, public interface, conceptual responsibility, and observable test stages—not invented model invocation counts or cosmetic line-count targets.
