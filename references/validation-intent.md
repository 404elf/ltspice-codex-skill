# Validation intent

Read this when authoring or changing the JSON passed to `run_validation_intent.py --intent`. Use these canonical field names even though the adapter also accepts common aliases, comments, trailing commas, and safe JSON-like literals. Ambiguous fields are rejected rather than guessed.

## Runnable nominal example

For a requested 1 kHz RC low-pass with a high-impedance output, use this NET:

```spice
* 1 kHz RC low-pass
.param R=1.59k C=100n
V1 in 0 AC 1
R1 in out {R}
C1 out 0 {C}
RLOAD out 0 1G
.ac dec 200 10 100k
.end
```

For a requested cutoff within 2% of 1 kHz, pair it with:

```json
{
  "mode": "QUICK",
  "analyses": {"ac": ".ac dec 200 10 100k"},
  "requirements": [
    {
      "name": "cutoff",
      "analysis": "ac",
      "measure": "fc_3db",
      "signal": "V(out)",
      "reference": "V(in)",
      "response": "lowpass",
      "target": 1000,
      "tolerance": 2
    }
  ],
  "required_nets": ["in", "out"]
}
```

These values illustrate a particular requirement; choose actual limits from the user's request or justified engineering assumptions.

## Top-level fields

| Field | Meaning |
| --- | --- |
| `mode` | `AUTO` (default), `QUICK`, `STANDARD`, `STRICT`, or `BATCH`; labels the agent's plan, without generating analyses or corners. |
| `analyses` | Named analysis directives, e.g. `{"ac": ".ac dec 200 10 100k", "bias": ".op"}`. An object can use `kind` and `directive`; `{"ac": {}}` reuses an unambiguous `.ac` in the NET. |
| `requirements` | List or named object of measurements and limits. Empty means execution-only validation. |
| `tolerances` | Percentage variation of named `.param` values; see below. |
| `required_nets` | Nodes that must exist, e.g. `["in", "out"]`; this checks names, not full topology. |
| `model_policy` | Optional `real_device_required` generic-model guard. |
| `preserve_save` | Boolean, default `false`; preserve `.save` only when requested. |

The parser recognizes `tran`, `ac`, `dc`, `op`, `noise`, `tf`, and `pz`. Recognition does not promise a usable RAW file for every analysis; normal success still requires RAW/LOG validation. Prefer explicit analyses. If omitted, only a single unambiguous NET analysis can be inferred by the suite, and requirement routing must still be explicit.

For multiple analyses, set each requirement's `analysis` to a unique analysis name. Requirements and tolerances may instead be nested inside their analysis object; do not mix nested and top-level forms for the same field.

An explicit directive updates the delivered NET before simulation when both the NET and plan have one unambiguous directive of that kind. For example, changing the plan's only `.ac` sweep also updates the canonical NET's sole `.ac` line; the separate input file is preserved. Supplemental kinds and multiple alternative sweeps are validation-only. In those cases, set the intended delivery directive in the NET explicitly instead of assuming the finalizer chooses one.

## Requirements and measurement limits

Each requirement needs `measure` and `signal`; use `name` for stable reporting and `analysis` for routing. Trace names are LTspice names such as `V(out)` or `I(R1)`, not arbitrary expressions.

| Measure | Extra fields / behavior |
| --- | --- |
| `final` | Last real sample; use for `.op` bias values. |
| `min`, `max`, `peak_to_peak` | Compute over all saved samples of the trace's real part. |
| `mean`, `rms` | For transient analyses, integrate over the saved time interval with linear interpolation between samples. Other analyses use the arithmetic sample mean or RMS. |
| `abs_max` | Maximum magnitude, including complex AC samples. |
| `value_at` | Requires `at`; returns the real value at the nearest saved axis sample. |
| `gain_at` | Requires `at` and `reference`; returns a linear magnitude ratio, not dB or phase. |
| `fc_3db` | `response` is `lowpass` (default) or `highpass`; normally supply the input trace as `reference`. Returns a sampled -3 dB edge relative to the maximum magnitude in the sweep. |

Use `min`/`max` for explicit bounds or `target` plus `tolerance` for a percentage band. `tolerance: 5` means 5%, not 0.05. Omitting tolerance with a target means zero tolerance; do not accidentally require exact floating-point equality. For a zero target, prefer explicit absolute `min`/`max` bounds. Numbers must be finite; Boolean values are not engineering quantities. Target/axis/bound values accept SPICE suffixes (`k`, `meg`, `m`, `u`, `n`, etc.); `m` means milli.

Component tolerances and requirement tolerances are different: one varies the circuit, the other defines acceptance. Do not interchange them.

A requirement's `scope` is `all` (default), `nominal`, or `corners`. When the user gives different nominal and corner limits, include both requirements in one plan, for example:

```json
{
  "analyses": {"ac": ".ac dec 200 10 100k"},
  "requirements": [
    {"name": "nominal_cutoff", "scope": "nominal", "analysis": "ac", "measure": "fc_3db", "signal": "V(out)", "reference": "V(in)", "target": 1000, "tolerance": 2},
    {"name": "corner_cutoff", "scope": "corners", "analysis": "ac", "measure": "fc_3db", "signal": "V(out)", "reference": "V(in)", "target": 1000, "tolerance": 20}
  ],
  "tolerances": {"parameters": {"R": 5, "C": 10}}
}
```

This uses the earlier RC NET. The nominal requirement is checked only at nominal values; the corner requirement is checked at each matching corner. Limits that apply everywhere, such as a common gain minimum, omit `scope`. A `corners` requirement without matching corner jobs is rejected before simulation. Never replace the tighter nominal gate with the wider corner gate or rely only on a final-answer calculation to retain it.

Measurement coverage matters:

- `value_at` and `gain_at` reject points outside the simulated axis range; choose adequate sample density inside it. They use the nearest sample, without interpolation.
- `fc_3db` needs a nonzero response and an observed crossing. Sweep across both the passband and stopband, and make frequency resolution small relative to the permitted error. Low-pass returns the first below-threshold sample after the peak; high-pass returns the last below-threshold sample before it. This metric does not establish a general band-pass or resonant-filter specification.
- Transient aggregate metrics include startup unless the analysis's saved interval excludes it. Use separate analyses when both startup and steady-state behavior matter. For example, `.tran 0 25m 20m 100n` simulates to 25 ms and saves 20-25 ms; the stop time is absolute, not the saved duration. `.tran 0 5m 20m` saves no useful interval and is rejected before simulation.
- Invalid/non-finite trace values or undefined references fail the measurement. Do not treat missing coverage as proof of compliance.

## Component tolerances

Parameters must appear in the NET and actually control the intended components, as `R` and `C` do in the example. For R ±5% and C ±10%, add:

```json
{
  "tolerances": {
    "parameters": {"R": 5, "C": 10}
  }
}
```

This is a fragment to merge into a complete intent, not a standalone plan. The example's 2% cutoff requirement will generally fail these component corners. Change the acceptance band only if the actual requested specification differs.

A parameter can also use `[-5, 10]` or `{"low": -5, "high": 10}` for asymmetric percentage bounds. Ungrouped tolerances run against the suite's primary analysis, not every analysis. To check several analyses, declare each group explicitly:

```json
{
  "tolerances": {
    "groups": {
      "ac": {"parameters": {"R": 5, "C": 10}},
      "tran": {"parameters": {"R": 5, "C": 10}}
    }
  }
}
```

Both named analyses must exist in the complete plan; each group's `analysis` is one name, not a list. `strategy: cartesian` expands endpoint combinations. `auto` uses ordinary endpoint combinations unless monotonic declarations are supplied; it does not prove monotonicity. Use `strategy: monotonic` only with a justified objective and directions, for example `objectives: {"cutoff": {"analysis": "ac", "directions": {"R": "inverse", "C": "inverse"}}}` for an RC cutoff. Endpoints alone do not establish all interior extrema of a nonlinear circuit.

For an explicit finite sweep or corner list that the intent schema cannot express, use the existing suite's `corners` interface deliberately; inspect its schema and validate it before simulation. Do not silently translate requested test points into a different endpoint plan.
