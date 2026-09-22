# Circuit checks before choosing analyses

Use the section relevant to the requested circuit. These checks help distinguish
topology and loading mistakes from simulator or model failures.

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
