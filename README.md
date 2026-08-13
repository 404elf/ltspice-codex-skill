# LTspice Codex Skill

Generate and validate LTspice circuits from natural language with Codex.

The workflow is:

```text
Natural language → Codex → SPICE NET → LTspice simulation → RAW/LOG validation → Weave → editable ASC schematic
```

This repository contains the Codex Skill instructions and deterministic helper scripts. It does not contain LTspice, proprietary Analog Devices models, or any other LTspice installation files.

## Beginner setup

1. Install LTspice separately from Analog Devices.
2. Give Codex this repository URL and ask:

   > Install and configure this LTspice simulation skill on this machine.

3. Ask Codex for a circuit, for example:

   > Design a 1 kHz Butterworth low-pass filter and simulate it with LTspice.

The bootstrap detects LTspice, creates an isolated Python environment, installs the pinned Python dependencies, obtains Weave from its upstream repository, installs Weave's npm dependency, runs a fresh RC smoke test, and saves local paths in an ignored configuration file.

## What is validated

- LTspice exit code is never accepted as the only proof of success.
- Every run requires a fresh RAW file and a fresh LOG without fatal/parser/simulation errors.
- Weave converts the exact simulated NET into an ASC and performs round-trip connectivity verification.
- `MATCH` means connectivity equivalence; it does not prove electrical correctness or the requested engineering target.
- In STRICT mode, the generated ASC is also run through LTspice.

The skill supports `AUTO`, `QUICK`, `STANDARD`, `STRICT`, and `BATCH` modes. NET is the source of truth. Ordinary parameter-only changes update the existing NET and replace the corresponding results; BATCH creates ASC files only for selected/final candidates.

## Manual installation and debugging

From a PowerShell prompt in this repository:

```powershell
py -3 bootstrap.py
```

The bootstrap writes `.ltspice-codex-config.json` beside this README. That file is local and ignored. To inspect the environment without running a circuit, use:

```powershell
py -3 bootstrap.py --check-only
```

The installed helper scripts are in `scripts/`. Use the configured Python executable and the detected paths from `.ltspice-codex-config.json`; do not copy machine-specific values into the skill files.

The bootstrap currently supports Windows. It deliberately stops when LTspice is not installed instead of downloading or redistributing it.

## Credits and licensing

This project uses Weave for NET-to-ASC conversion and connectivity verification, PyLTSpice/spicelib for optional RAW parsing, and elkjs through Weave's npm package. LTspice is supplied separately by Analog Devices and is not included here. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for upstream links and the practical licensing approach.

The project code is licensed under GPL-3.0-or-later; see [LICENSE](LICENSE).
