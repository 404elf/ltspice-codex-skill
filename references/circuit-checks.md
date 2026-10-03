# Circuit checks before choosing values and analyses

Use the section relevant to the requested circuit. These checks help distinguish
topology and loading mistakes from simulator or model failures.

## Component selection

For a new practical design without fixed values, prefer common commercial nominal R/L/C values. Retain explicitly supplied values, component counts/topology, specified impedances (such as a 50-ohm termination), and existing values outside an authorized redesign. Ideal transfer-function synthesis or a request for exact calculated values may retain non-preferred values. Do not round source settings, frequencies, timing, or acceptance limits as though they were component values. Source and measurement-load equivalents are simulation models; distinguish them from physical BOM parts when sourcing matters.

Use calculated ideal values to guide a small set of plausible candidates. For resistors, start with common E24 values and use E96 or another offered series when finer adjustment is needed. For capacitors and inductors, use nominal values offered by the intended device family; membership in a preferred-number series alone does not establish availability. See [Vishay's preferred-value table](https://www.vishay.com/docs/31001/dectable.pdf). With a high-impedance load, 1.6 kOhm and 100 nF are practical nominal candidates for a 1 kHz RC low-pass; check the actual loaded circuit and requested error band.

Select values before creating the final canonical NET and validation evidence. Recheck the original measurements after substitution: rounding each part independently can change cutoff, ripple, gain, or impedance matching, especially in higher-order LC filters. Jointly adjust permitted values when useful. Prefer a finer available series before adding combinations; series/parallel combinations require topology freedom and a clear engineering reason. Keep the user's acceptance limits unchanged. If the explored practical candidates cannot meet them, report the measured shortfall and remaining options instead of presenting an ideal-value result as a practical PASS.

Distinguish the scope of the result:

- For nominal simulation, common nominal values do not by themselves verify component tolerance, sourcing, or hardware performance. Plan relevant tolerances as required by the task; do not add a fixed corner suite to every simple request.
- For a requested buildable design or BOM, check the actual chosen family/part for voltage/current rating, package, tolerance, and consequential parasitics. For inductors, evaluate Q, DCR, self-resonant frequency, and saturation/current capability where relevant; a common inductance alone is insufficient. See [Coilcraft's RF inductor selection guidance](https://www.coilcraft.com/getmedia/42895920-090c-47c1-b3d0-03b0cd30d429/doc671_selecting_rf_inductors.pdf). Claim current stock or procurement availability only when checked.

When a less common or calculated value remains in the delivery, briefly explain the constraint that requires it when material to the user's intended use. Keep the full chosen precision through parameter display; visual formatting must not silently change the circuit.

## Loaded filters and op-amp feedback

For a series R / shunt C low-pass driven by a low-impedance source, a resistive
load changes both DC gain and cutoff:

- DC gain is `Rload / (R + Rload)`.
- The pole is `1 / (2*pi*(R || Rload)*C)`.

Include every significant load, including a feedback divider. Trace where the
amplifier senses its output: putting the RC inside the feedback loop changes the
closed-loop response, so its standalone RC cutoff no longer sets the delivered
cutoff. An input filter followed by an amplifier is another option when its
source and input loading are acceptable; retain any topology the user specified.

Check the amplifier's own output pin as well as the delivered output. A series
resistor may require much more drive voltage than the load voltage. Before
blaming the model for clipping, calculate the resistor drops and check supply
headroom, model pin order, and feedback. Use the specified real model for the
final DC/AC/transient checks.

## Full-wave bridge and floating references

Identify both AC terminals and both DC terminals in the NET. The reservoir
capacitor and load must connect across the bridge's DC terminals. Grounding the
DC negative terminal gives a single-ended output measurement. Keep both AC
terminals floating relative to that ground; the conducting diode pair supplies
the return path. Do not connect an AC terminal directly to DC ground: a zero-volt
"reference" voltage source also creates this short and bypasses part of the
bridge, even when the measured voltage and ripple meet loose limits.

Trace the two conducting-diode paths, one for each source polarity. A numerical
PASS alone does not establish full-wave operation or the requested topology.
Choose separate startup and settled intervals when both are specified.

## Open-loop buck startup

Check the power path through the switch, inductor, load and freewheel diode.
An uncharged LC can overshoot well above the steady output under fixed PWM.
Increasing capacitance to reduce ripple can make that overshoot worse.

For an ideal LC with a parallel resistive load, the natural angular frequency
is `1/sqrt(L*C)` and damping ratio is `sqrt(L/C)/(2*Rload)`. Treat these as initial
estimates: switch/diode losses, parasitics and discontinuous conduction affect
the result. Select L/C and any justified damping or startup control against both
startup and ripple requirements, then simulate the actual chosen circuit.
Do not initialize the capacitor at its final voltage when uncharged startup is
required. Keep startup extrema and steady mean/ripple as separate gates.
