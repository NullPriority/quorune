from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
import zipfile

from quorune.python_runtime import REQUIRES_PYTHON, require_supported_python
from packaging.specifiers import InvalidSpecifier, SpecifierSet


def _wheel_requires_python(wheel: Path) -> str | None:
    with zipfile.ZipFile(wheel) as archive:
        metadata_paths = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_paths) != 1:
            raise ValueError(
                f"Expected one wheel METADATA file, found {len(metadata_paths)}"
            )
        for line in archive.read(metadata_paths[0]).decode("utf-8").splitlines():
            if line.startswith("Requires-Python: "):
                return line.removeprefix("Requires-Python: ")
    return None


def _requires_python_matches(observed: str | None) -> bool:
    if observed is None:
        return False
    try:
        return SpecifierSet(observed) == SpecifierSet(REQUIRES_PYTHON)
    except InvalidSpecifier:
        return False


def _assert_namespace_contents(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    if "quorune/__init__.py" not in names:
        raise SystemExit("Wheel does not contain the quorune package")
    legacy = sorted(
        name for name in names if name.startswith("mtg_commander_sim/")
    )
    if legacy:
        raise SystemExit(
            "Wheel contains the retired mtg_commander_sim namespace: "
            f"{legacy[0]}"
        )


def main() -> int:
    require_supported_python()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    wheels = sorted(args.dist.glob("quorune-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"Expected exactly one project wheel, found {len(wheels)}")
    wheel = wheels[0].resolve()
    _assert_namespace_contents(wheel)
    observed_requirement = _wheel_requires_python(wheel)
    if not _requires_python_matches(observed_requirement):
        raise SystemExit(
            f"Wheel Requires-Python must be {REQUIRES_PYTHON!r}; "
            f"found {observed_requirement!r}"
        )
    with tempfile.TemporaryDirectory() as temporary:
        workdir = Path(temporary)
        environment = workdir / "wheel-venv"
        smoke_environment = os.environ.copy()
        smoke_environment.pop("PYTHONPATH", None)
        smoke_environment.pop("PYTHONHOME", None)
        run_options = {"cwd": workdir, "env": smoke_environment}
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = (
            environment / "Scripts" / "python.exe"
            if sys.platform == "win32"
            else environment / "bin" / "python"
        )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(wheel),
            ],
            check=True,
            **run_options,
        )
        subprocess.run(
            [
                str(python),
                "-c",
                (
                    "from importlib.metadata import distribution; "
                    "from pathlib import Path; "
                    "import quorune as package; "
                    "module = Path(package.__file__).resolve(); "
                    "installed = Path(distribution('quorune').locate_file("
                    "'quorune/__init__.py')).resolve(); "
                    "assert module == installed, (module, installed); "
                    "print(package.__version__)"
                ),
            ],
            check=True,
            **run_options,
        )
        subprocess.run(
            [str(python), "-m", "quorune", "--help"],
            check=True,
            **run_options,
            stdout=subprocess.DEVNULL,
        )
        scripts = environment / ("Scripts" if sys.platform == "win32" else "bin")
        executable_suffix = ".exe" if sys.platform == "win32" else ""
        for command in ("quorune", "simctl"):
            subprocess.run(
                [str(scripts / f"{command}{executable_suffix}"), "--help"],
                check=True,
                **run_options,
                stdout=subprocess.DEVNULL,
            )
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--upgrade",
                str(wheel),
            ],
            check=True,
            **run_options,
        )
        subprocess.run(
            [str(scripts / f"quorune-server{executable_suffix}"), "--help"],
            check=True,
            **run_options,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                str(python),
                "-c",
                (
                    "from importlib.metadata import distribution; "
                    "entries = {entry.name: entry.value for entry in "
                    "distribution('quorune').entry_points "
                    "if entry.group == 'console_scripts'}; "
                    "assert entries['quorune'] == "
                    "'quorune.cli:main', entries; "
                    "assert entries['simctl'] == "
                    "'quorune.cli:main', entries; "
                    "assert entries['quorune-server'] == "
                    "'server.__main__:main', entries"
                ),
            ],
            check=True,
            **run_options,
        )
    print(
        "Verified clean installation, simulation CLI, and server entry point "
        f"for {wheel.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
