#!/usr/bin/env python3
"""Bootstrap the portable LTspice Codex Skill on Windows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / ".ltspice-codex-config.json"
REQUIREMENTS = ROOT / "requirements.txt"
WEAVE_REPOSITORY = "https://github.com/senolgulgonul/weave.git"
WEAVE_COMMIT = "feb1f2bd9019d966fcaa12f276936299267c798b"
WEAVE_ZIP = f"https://github.com/senolgulgonul/weave/archive/{WEAVE_COMMIT}.zip"
WEAVE_ELKJS_VERSION = "0.9.3"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command))
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def first_file(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def detect_ltspice() -> Path | None:
    candidates: list[Path] = []
    for key in ("LTSPICE_EXE", "LTSPICE_PATH"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value).expanduser())
    for command in ("LTspice.exe", "XVIIx64.exe", "XVIIx86.exe"):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))

    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData"),
    ]
    layouts = [
        ("Analog Devices", "LTspice", "LTspice.exe"),
        ("Analog Devices", "LTspice", "XVIIx64.exe"),
        ("LTC", "LTspiceXVII", "XVIIx64.exe"),
        ("LTC", "LTspiceXVII", "XVIIx86.exe"),
        ("LTC", "LTspice", "XVIIx64.exe"),
        ("Programs", "LTspice", "LTspice.exe"),
    ]
    for root in roots:
        if not root:
            continue
        base = Path(root)
        candidates.extend(base.joinpath(*layout) for layout in layouts)
    return first_file(candidates)


def detect_node() -> tuple[Path, Path, str]:
    node_candidates = [Path(x) for x in filter(None, [shutil.which("node")])]
    for root in filter(None, [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]):
        node_candidates.append(Path(root) / "nodejs" / "node.exe")
    node = first_file(node_candidates)
    if not node:
        fail("Node.js 18 or newer is required; install it and run bootstrap again.")
    version = run([str(node), "--version"])
    if version.returncode != 0:
        fail("could not determine the Node.js version")
    raw_version = version.stdout.strip().lstrip("v")
    try:
        major = int(raw_version.split(".", 1)[0])
    except ValueError:
        fail(f"unrecognized Node.js version: {raw_version}")
    if major < 18:
        fail(f"Node.js 18 or newer is required; found {raw_version}")

    npm_candidates = [node.with_name("npm.cmd"), node.with_name("npm.exe")]
    npm_found = shutil.which("npm.cmd") or shutil.which("npm")
    if npm_found:
        npm_candidates.insert(0, Path(npm_found))
    npm = first_file(npm_candidates)
    if not npm:
        npm = first_file([Path(x) for x in filter(None, [shutil.which("npm.cmd"), shutil.which("npm")])])
    if not npm:
        fail("npm was not found beside Node.js")
    return node, npm, raw_version


def venv_python(venv_dir: Path) -> Path:
    return venv_dir / "Scripts" / "python.exe"


def dependencies_ready(python: Path) -> bool:
    check = run([
        str(python), "-c",
        "import importlib.metadata as m; assert m.version('PyLTSpice') == '6.0.1'; assert m.version('spicelib') == '1.6.3'; import numpy",
    ])
    return check.returncode == 0


def configure_python() -> Path:
    venv_dir = ROOT / ".venv"
    python = venv_python(venv_dir)
    if not python.is_file():
        print("Creating the isolated Python environment...")
        venv.EnvBuilder(with_pip=True, clear=False).create(venv_dir)
    if not dependencies_ready(python):
        result = run([
            str(python), "-m", "pip", "install", "--disable-pip-version-check",
            "--no-input", "-r", str(REQUIREMENTS),
        ])
        if result.returncode != 0:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            fail("Python dependency installation failed")
    return python.resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_revision(git: str, root: Path) -> str | None:
    result = run([git, "rev-parse", "HEAD"], cwd=root)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def pin_weave_checkout(git: str, root: Path) -> str:
    revision = git_revision(git, root)
    if revision != WEAVE_COMMIT:
        fetch = run([git, "fetch", "--depth", "1", "origin", WEAVE_COMMIT], cwd=root)
        if fetch.returncode != 0:
            print(fetch.stdout, end="")
            print(fetch.stderr, end="", file=sys.stderr)
            fail(f"could not fetch the pinned Weave commit {WEAVE_COMMIT}")
        checkout = run([git, "checkout", "--detach", WEAVE_COMMIT], cwd=root)
        if checkout.returncode != 0:
            print(checkout.stdout, end="")
            print(checkout.stderr, end="", file=sys.stderr)
            fail(f"could not check out the pinned Weave commit {WEAVE_COMMIT}")
        revision = git_revision(git, root)
    if revision != WEAVE_COMMIT:
        fail(f"Weave checkout is not pinned to {WEAVE_COMMIT}")
    return revision


def clone_pinned_weave(git: str, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    init = run([git, "init"], cwd=root)
    if init.returncode != 0:
        print(init.stdout, end="")
        print(init.stderr, end="", file=sys.stderr)
        fail("could not initialize the Weave checkout")
    remote = run([git, "remote", "add", "origin", WEAVE_REPOSITORY], cwd=root)
    fetch = run([git, "fetch", "--depth", "1", "origin", WEAVE_COMMIT], cwd=root)
    checkout = run([git, "checkout", "--detach", WEAVE_COMMIT], cwd=root)
    if any(item.returncode != 0 for item in (remote, fetch, checkout)):
        for item in (remote, fetch, checkout):
            if item.returncode != 0:
                print(item.stdout, end="")
                print(item.stderr, end="", file=sys.stderr)
        fail("could not obtain the pinned Weave commit from its official repository")


def extract_weave_archive(archive: Path, tools_dir: Path, weave_root: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as package:
            weave_entries = [name for name in package.namelist() if name.endswith("/cli/weave.js")]
            if not weave_entries:
                fail("the official Weave archive did not contain cli/weave.js")
            extracted_root = tools_dir / Path(weave_entries[0]).parts[0]
            package.extractall(tools_dir)
        if not (extracted_root / "cli" / "weave.js").is_file():
            fail("the extracted Weave archive did not contain cli/weave.js")
        extracted_root.rename(weave_root)
    except (OSError, zipfile.BadZipFile) as exc:
        fail(f"could not extract Weave: {exc}")


def obtain_weave() -> tuple[Path, str, str, str]:
    tools_dir = ROOT / "tools"
    weave_root = tools_dir / "weave"
    cli_dir = weave_root / "cli"
    weave_js = cli_dir / "weave.js"
    tools_dir.mkdir(exist_ok=True)

    if not weave_js.is_file():
        if weave_root.exists():
            fail(f"Weave directory exists but is incomplete: {weave_root}")
        git = shutil.which("git")
        if git:
            clone_pinned_weave(git, weave_root)
        else:
            archive = tools_dir / f"weave-{WEAVE_COMMIT}.zip"
            print("Git was not found; downloading Weave's official source archive...")
            try:
                urllib.request.urlretrieve(WEAVE_ZIP, archive)
            except Exception as exc:
                fail(f"could not obtain Weave: {exc}")
            try:
                extract_weave_archive(archive, tools_dir, weave_root)
            finally:
                archive.unlink(missing_ok=True)
    if not weave_js.is_file():
        fail("Weave CLI was not found after setup")

    git = shutil.which("git")
    if git and (weave_root / ".git").is_dir():
        weave_revision = pin_weave_checkout(git, weave_root)
    elif (weave_root / ".git").is_dir():
        fail("Weave's Git checkout could not be inspected")
    else:
        weave_revision = WEAVE_COMMIT
    try:
        package_data = json.loads((cli_dir / "package.json").read_text(encoding="utf-8"))
        weave_version = str(package_data.get("version", "unknown"))
        elkjs_version = WEAVE_ELKJS_VERSION
    except (OSError, json.JSONDecodeError, AttributeError):
        weave_version = "unknown"
        elkjs_version = "unknown"
    return cli_dir.resolve(), weave_revision, weave_version, elkjs_version


def configure_weave(cli_dir: Path, npm: Path) -> None:
    package = cli_dir / "package.json"
    if not package.is_file():
        fail(f"Weave CLI package metadata is missing: {package}")
    try:
        package_data = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"could not read Weave package metadata: {exc}")
    dependencies = package_data.setdefault("dependencies", {})
    exact_spec = WEAVE_ELKJS_VERSION
    if dependencies.get("elkjs") != exact_spec:
        dependencies["elkjs"] = exact_spec
        package.write_text(json.dumps(package_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lock = cli_dir / "package-lock.json"

    def lock_matches() -> bool:
        if not lock.is_file():
            return False
        try:
            lock_data = json.loads(lock.read_text(encoding="utf-8"))
            root_deps = lock_data.get("packages", {}).get("", {}).get("dependencies", {})
            installed = lock_data.get("packages", {}).get("node_modules/elkjs", {}).get("version")
            return root_deps.get("elkjs") == exact_spec and installed == WEAVE_ELKJS_VERSION
        except (OSError, json.JSONDecodeError, AttributeError):
            return False

    if not lock_matches():
        result = run([
            str(npm), "install", "--ignore-scripts", "--no-audit", "--no-fund",
            "--package-lock-only", "--save-exact", f"elkjs@{WEAVE_ELKJS_VERSION}",
        ], cwd=cli_dir)
        if result.returncode != 0:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            fail("could not create a reproducible Weave npm lockfile")

    elkjs = cli_dir / "node_modules" / "elkjs"
    installed_version = None
    installed_package = elkjs / "package.json"
    if installed_package.is_file():
        try:
            installed_version = json.loads(installed_package.read_text(encoding="utf-8")).get("version")
        except (OSError, json.JSONDecodeError):
            installed_version = None
    if not lock_matches() or installed_version != WEAVE_ELKJS_VERSION:
        result = run([
            str(npm), "ci", "--ignore-scripts", "--no-audit", "--no-fund",
        ], cwd=cli_dir)
        if result.returncode != 0:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            fail("Weave npm dependency installation failed")
    if not (cli_dir / "node_modules" / "elkjs" / "package.json").is_file():
        fail("Weave npm dependency installation did not produce elkjs")


def write_config(*, python: Path, ltspice: Path, node: Path, npm: Path,
                 weave_cli: Path, weave_source: str, weave_version: str,
                 elkjs_version: str) -> dict[str, object]:
    config: dict[str, object] = {
        "schema_version": 1,
        "python": str(python),
        "ltspice": str(ltspice),
        "node": str(node),
        "npm": str(npm),
        "weave_cli": str(weave_cli),
        "weave_source": WEAVE_REPOSITORY,
        "weave_revision": weave_source,
        "weave_expected_commit": WEAVE_COMMIT,
        "weave_version": weave_version,
        "elkjs_version": elkjs_version,
        "output_root": str((ROOT / "outputs").resolve()),
        "configured_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config


def smoke_test(config: dict[str, object]) -> None:
    output = ROOT / "outputs" / "bootstrap-smoke-rc"
    output.mkdir(parents=True, exist_ok=True)
    net = output / "quick_rc.net"
    shutil.copy2(ROOT / "tests" / "quick_rc.net", net)
    asc = output / "quick_rc.asc"
    report = output / "quick_rc.run-report.json"
    verification = output / "quick_rc.weave-verification.txt"
    python = Path(str(config["python"]))
    ltspice = str(config["ltspice"])
    node = str(config["node"])
    weave_cli = str(config["weave_cli"])

    run_result = run([
        str(python), str(ROOT / "scripts" / "run_ltspice.py"),
        "--input", str(net), "--ltspice", ltspice, "--report", str(report),
    ], cwd=output)
    if run_result.returncode != 0:
        print(run_result.stdout, end="")
        print(run_result.stderr, end="", file=sys.stderr)
        fail("LTspice RC smoke test failed")
    raw = output / "quick_rc.raw"
    log = output / "quick_rc.log"
    if not raw.is_file() or not log.is_file():
        fail("RC smoke test did not produce fresh RAW and LOG files")
    log_check = run([
        str(python), str(ROOT / "scripts" / "validate_log.py"), "--log", str(log),
    ], cwd=output)
    if log_check.returncode != 0:
        print(log_check.stdout, end="")
        print(log_check.stderr, end="", file=sys.stderr)
        fail("RC smoke-test LOG validation failed")
    weave_result = run([
        str(python), str(ROOT / "scripts" / "weave_convert.py"),
        "--net", str(net), "--weave-dir", weave_cli, "--node", node,
        "--asc", str(asc), "--result", str(verification), "--force",
    ], cwd=output)
    if weave_result.returncode != 0 or not asc.is_file():
        print(weave_result.stdout, end="")
        print(weave_result.stderr, end="", file=sys.stderr)
        fail("Weave RC smoke test failed")
    verification_text = verification.read_text(encoding="utf-8", errors="replace")
    if "VERDICT=MATCH" not in verification_text:
        fail("Weave RC smoke test did not return MATCH")
    print(f"Smoke test passed: {output}")


def check_only() -> None:
    if not CONFIG_PATH.is_file():
        print("No local configuration found. Run bootstrap.py to configure the skill.")
        return
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    missing = [key for key in ("python", "ltspice", "node", "npm", "weave_cli")
               if not Path(str(config[key])).exists()]
    if missing:
        fail("configured paths are missing: " + ", ".join(missing))
    print(json.dumps(config, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure this portable LTspice Codex Skill.")
    parser.add_argument("--check-only", action="store_true", help="check existing local configuration")
    parser.add_argument("--skip-smoke-test", action="store_true", help="configure without running the RC smoke test")
    args = parser.parse_args()
    if args.check_only:
        check_only()
        return 0

    ltspice = detect_ltspice()
    if not ltspice:
        fail("LTspice was not found. Install LTspice separately from Analog Devices, then run bootstrap.py again.")
    print(f"LTspice: {ltspice}")
    print(f"Python: {sys.executable}")
    python = configure_python()
    node, npm, node_version = detect_node()
    print(f"Node.js: {node} ({node_version})")
    weave_cli, weave_source, weave_version, elkjs_version = obtain_weave()
    configure_weave(weave_cli, npm)
    config = write_config(
        python=python, ltspice=ltspice, node=node, npm=npm,
        weave_cli=weave_cli, weave_source=weave_source,
        weave_version=weave_version, elkjs_version=elkjs_version,
    )
    if not args.skip_smoke_test:
        smoke_test(config)
    print(f"Configuration saved: {CONFIG_PATH}")
    print(f"Output root: {config['output_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
