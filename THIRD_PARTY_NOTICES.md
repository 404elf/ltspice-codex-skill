# Third-party notices

This file records the practical licensing approach for this repository. It is not legal advice. Third-party packages are installed externally during bootstrap; their source code is not vendored here.

| Project | Upstream | License | Distribution in this repository |
|---|---|---|---|
| Weave CLI | https://github.com/senolgulgonul/weave | MIT for the CLI according to its current `cli/package.json` and README; the repository API does not expose an SPDX license | Not bundled; cloned or downloaded at bootstrap time from the official repository. |
| elkjs | https://github.com/kieler/elkjs | EPL-2.0 | Not bundled; installed by Weave's npm package installation. |
| PyLTSpice | https://github.com/nunobrum/PyLTSpice | GPL-3.0 | Not bundled; installed from the Python package index. |
| spicelib | https://github.com/nunobrum/spicelib | GPL-3.0 | Not bundled; installed as a PyLTSpice dependency from the Python package index. |
| LTspice | https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html | Proprietary external software | Never bundled or redistributed; users install it separately from Analog Devices. |

## Attribution and practical notes

- Weave is credited to Senol Gulgonul. The current CLI metadata identifies the CLI as MIT-licensed, while the GitHub repository metadata does not expose an SPDX license; this practical notice follows the CLI metadata and upstream README. Its required `elkjs` dependency remains external.
- `elkjs` is credited to the Eclipse Layout Kernel project and remains under EPL-2.0 as an external dependency.
- PyLTSpice and spicelib are credited to their upstream maintainers and are GPL-3.0 projects. This repository uses GPL-3.0-or-later for its own code to take a conservative, GPL-compatible approach to the direct Python integration.
- LTspice and any Analog Devices models or libraries are proprietary external materials. This repository contains no LTspice executable, installation files, proprietary model files, or Analog Devices library files.

Check the upstream repositories and package metadata at installation or release time for current versions and notices. If a downstream distribution bundles external dependencies, it must provide the corresponding notices and license texts required by those dependencies.
