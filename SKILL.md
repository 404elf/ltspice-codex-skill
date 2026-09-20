---
name: ltspice-sim-v2
description: Design, edit, simulate, and validate LTspice circuits; measure RAW/LOG results, check component tolerances, and convert verified NET files into ASC schematics with Weave. Use for requested LTspice circuit work and simulation troubleshooting.
---

# LTspice Simulation v2

Use the user's requested scope: circuit design, edits, simulation, result inspection, or troubleshooting. A theory explanation or review alone does not require creating a circuit. This skill is independent of the legacy LTSPICE-AI project and `ltspice-circuit-simulator` skill.

## Start

Read `.ltspice-codex-config.json` beside this file for Python, LTspice, Weave, and the output root. Reuse valid configuration; no routine bootstrap, dependency reinstall, or helper-source inspection. For missing configuration or tool failures, read [setup and troubleshooting](references/operations.md).

The canonical `.net`/`.cir` reported by the runner is the circuit source of truth. Plan topology, values, loads, analyses, quantitative requirements, and relevant tolerances before running. Preserve explicit user constraints; state reasonable assumptions where details do not affect the decision.

- For an unspecified high-impedance voltage output, add a high-value `RLOAD` to ground. Do not add it to internal, storage, sensing, intentionally floating, or current-output nodes. Include any consequential load in the design and report.
- Use the lightest complete plan: `QUICK` for simple nominal checks; `STANDARD` for ordinary analog/tolerance work; `STRICT` for switching, power, nonlinear, or critical feedback work; `BATCH` for repeated candidates. These are planning labels, not successive validation stages.
- **The runner executes only the declared analyses, requirements, and tolerances.** `AUTO` does not infer them; `STRICT` does not add checks; `BATCH` does not generate candidates. Decide the coverage yourself and avoid duplicate nominal runs.
- When a concrete device is required, use its readable model and `model_policy: real_device_required`. This rejects known generic placeholders; it does not certify model provenance or accuracy.

## Validate

1. Create or update the NET with explicit ground `0`, unique references, usable model dependencies, a runnable analysis directive, and `.end`. Before replacing existing user files, verify a rollback copy outside the delivery directory. Use the same delivery paths for ordinary updates.
2. Read [validation intent](references/validation-intent.md) when writing an intent. Save the small JSON plan in the support directory. If nominal and corner limits differ, keep separate requirements with `scope: nominal` and `scope: corners` in that same plan; default `scope: all` applies a limit to both.
3. Run one complete plan using absolute paths:

   ```powershell
   & '<configured Python>' '<skill root>\scripts\run_validation_intent.py' --net '<absolute NET>' --intent '<absolute intent.json>'
   ```

4. Read the compact result first: `status`, `failure_class`, `failed_requirements`, `summary_path`, `ltspice_calls`, and `evidence_reused`. Use its `canonical_net` for every subsequent step. Inspect the summary for measurements, artifact paths, and failure reasons; open RAW/LOG only when diagnosis or requested analysis needs them.
5. Fix the demonstrated cause and rerun the affected plan. Never remove requirements, loosen targets, omit requested corners, substitute models, or change the topology just to obtain PASS. A range/coverage failure can require a better analysis rather than different components. Do not repeat an unchanged failing command without new evidence.

The helper normalizes safe representation errors, checks the plan, stages dependencies, runs the existing suite, and manages evidence. Do not recreate these mechanisms in ad hoc scripts. New simulation jobs require fresh RAW/LOG and valid logs even when LTspice exits with code 0. Let the suite reuse fingerprint-matched evidence; old files alone are not proof. When only acceptance targets or limits change and the existing sweep covers the measurements, update those requirements and rerun the same plan to re-evaluate the evidence. Do not change sampling, directives, or circuit values merely to force a fresh simulation. Report zero new LTspice calls and the reused-evidence count when that occurs.

For each existing analysis kind, the runner updates the canonical NET when the intent supplies one unambiguous explicit directive. This keeps ordinary sweep-range changes in the delivered ASC. Supplemental analysis kinds and alternative sweeps remain validation-only; choose the delivery analysis deliberately in those cases. Confirm the delivered analysis covers the measurements the user needs to reproduce.

`.save` directives are removed from the canonical NET by default. Set `preserve_save: true` only when the user requests saved traces or restricted RAW variables, and include every trace needed by the requirements.

## Finalize requested schematics

After electrical PASS, convert that exact canonical NET once:

```powershell
& '<configured Python>' '<skill root>\scripts\weave_convert.py' --net '<canonical_net>' --asc '<expected_asc>' --result '<expected_weave_result>'
```

Use paths from the compact result; `expected_asc` and `expected_weave_result` are destinations, not evidence that files already exist. For an existing ASC, add `--force` only after preserving its rollback copy. Do not hand-author or repair ASC coordinates.

The finalizer must exit successfully with `WEAVE_VERDICT=MATCH`, `ASC_SMOKE=PASS`, and `VERDICT=MATCH`. It runs the one required LTspice smoke check for every delivered ASC; do not repeat the engineering suite for that check. Connectivity MATCH alone is insufficient. In BATCH, finalize only the selected candidates.

## Deliver

Keep `<circuit>.asc` at the circuit-directory root and supporting files in `<circuit>_files/`: canonical NET, intent, RAW/LOG, summaries, models, plots, and verification. The runner supplies the exact paths. Root-level NET/RAW/LOG sidecars from manual LTspice runs are not canonical evidence.

Report requested measurements and gate results, with links to the output directory, canonical NET, generated ASC, validation RAW/LOG, summary, and Weave result as applicable. Report failures as failures. PASS proves only the declared checks; a run without quantitative requirements proves execution, not compliance with unstated design targets. Stop once the requested checks and deliverables pass.
