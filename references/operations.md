# Setup and troubleshooting

Read only for installation/configuration work, helper failures, unsupported input, or implementation changes. Normal circuit work uses the intent entrypoint without rereading helper internals.

## Configuration

The local `.ltspice-codex-config.json` resolves paths relative to its own directory; normal installed configurations use absolute paths. Check existing configuration with an available Python:

```powershell
py -3 '<skill root>\bootstrap.py' --check-only
```

If missing or invalid, use `py -3 '<skill root>\bootstrap.py'` to set up the available dependencies, then recheck. Bootstrap may install Python packages and fetch the pinned Weave revision. Respect existing authorization and network/filesystem permissions; do not repeatedly reinstall dependencies for an engineering failure. LTspice itself must already be installed separately.

The intent runner uses `python`, `ltspice`, and `output_root`; the finalizer also uses configured `node` and `weave_cli`. Both entrypoints accept `--config '<config.json>'` for an explicitly needed alternate configuration. Do not change shared configuration simply to redirect one test run.

When updating a configured installation to a new Weave pin, install and check that revision separately, then update only the configured Weave CLI path. Bootstrap keeps each pinned checkout in its own tool directory; keep the previous CLI available until the new path passes finalization.

Use absolute script and input paths. Relative NET/intent paths resolve from the current working directory, whereas configuration paths resolve from the configuration directory. For a new circuit, placing its NET in `<output_root>/<circuit>/<circuit>_files/` avoids a redundant source copy. An input outside `output_root` is promoted to `<output_root>/<net-stem>/`; use the returned canonical path thereafter.

## Diagnose the actual failure

| Evidence | Next action |
| --- | --- |
| `stage: intent`, missing/ambiguous field, bad path | Correct representation or configuration; no LTspice call is needed to discover this. |
| Dry-run/dependency failure | Read summary errors. Resolve the stated analysis, parameter, or dependency issue without changing engineering requirements. |
| RAW/LOG/parser/fatal/timeout failure | Inspect the affected job's log and run report; a simulator exit code alone is insufficient. |
| Failed numeric requirement | Inspect measured value, coverage, and failing corner; adjust the justified analysis or design. |
| `no -3 dB crossing` / `outside simulated range` | Extend the analysis range or improve coverage; do not substitute the sweep boundary as a measurement. |
| Weave mismatch or ASC smoke failure | Read the verification report and ASC run report. Preserve electrical validation evidence; repair the conversion/model cause, then rerun finalization. |
| `LAYOUT_FAILED` or layout warnings | Read `LAYOUT_REPORT` for attempted modes, overlapping instances, unknown geometry, label moves, `selected.display`, and estimated text conflicts. The adapter tries bounded Weave alternatives, moves labels along existing wires, and can use a short verified lead when sliding a label does not clear it. For crowded passive values, set the intent's `schematic_display` to `compact` and rerun validation before finalizing the returned canonical NET; do not edit a validated NET after PASS. Inspect the final ASC when readability matters. |

`failure_class` is a routing hint, not a definitive circuit diagnosis. Read the actual error before choosing an engineering change. After a failed attempted correction, retry only when a new identified cause or changed input justifies it; otherwise report the blocker and required missing information.

Readable `.lib`/`.include` dependencies can be staged under the support directory. Binary or non-text model assets may not be stageable; require a usable model or a targeted dependency fix. Do not rewrite the LTspice installation, silently drop dependencies, or substitute a generic device.

## Focused helpers

Use these only when the intent workflow does not cover the requested operation or when diagnosing a failure:

```powershell
& '<configured Python>' '<skill root>\scripts\run_ltspice.py' --input '<net-or-asc>' --ltspice '<LTspice.exe>'
& '<configured Python>' '<skill root>\scripts\run_validation_suite.py' --net '<net>' --spec '<canonical-spec.json>' --ltspice '<LTspice.exe>' --output '<support-dir>' --dry-run
& '<configured Python>' '<skill root>\scripts\parse_raw.py' --raw '<raw>' --trace '<name>'
```

The suite's `--dry-run` validates a canonical suite spec; it does not accept an engineering intent and does not prove electrical behavior. A successful intent run writes `validation_spec.json` in its support directory. Derived analyses can have multiple RAW/LOG pairs; get their paths from the summary rather than guessing a single filename.

For implementation changes, keep the intent adapter thin: normalization, configuration/path resolution, delegation to `run_validation_suite.py`, and compact reporting. Preserve the suite as the owner of simulation, metrics, corners, convergence handling, and evidence reuse. Run the existing unit tests and a targeted real simulation when changing those behaviors.
