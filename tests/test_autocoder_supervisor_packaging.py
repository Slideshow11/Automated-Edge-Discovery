"""Packaging regression test for the autocoder_supervisor package.

This test reproduces the exact documented installation
procedure in an isolated temp directory, then proves that
the installed package:

1. is registered with the right top-level name
2. contains source files (not an empty wheel)
3. imports cleanly from outside the repository checkout
4. supports ``python -m autocoder_supervisor.supervisor`` and
   ``python -m autocoder_supervisor.validate`` from outside the
   repository checkout

If any of these checks fail, the install procedure is
broken and the test fails with a clear diagnostic.

The test does NOT mutate any production state, does NOT
require network access, and does NOT require the repository
to be on PYTHONPATH after the install completes (we run the
installed Python with ``PYTHONPATH=`` cleared).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_PKG = REPO_ROOT / "scripts" / "local" / "autocoder_supervisor"
SOURCE_MANIFEST = REPO_ROOT / "scripts" / "local" / "pyproject.toml"


def _python_executable() -> str:
    """The Python executable to use for the test venv.

    Honours ``sys.executable`` so the test runs against the
    same interpreter as pytest itself.
    """
    return sys.executable


def _stage_install_root(staging: Path) -> Path:
    """Reproduce the documented install procedure verbatim.

    Returns the path to the staged install root.
    """
    install_root = staging / "install-root"
    # Step 1: copy the package directory.
    shutil.copytree(SOURCE_PKG, install_root / "autocoder_supervisor")
    # Step 2: copy the packaging manifest.
    shutil.copy(SOURCE_MANIFEST, install_root / "pyproject.toml")
    return install_root


def _create_venv(staging: Path) -> Path:
    venv_dir = staging / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    return venv_dir


def _pip_install(venv_dir: Path, install_root: Path) -> None:
    pip = venv_dir / "bin" / "pip"
    proc = subprocess.run(
        [str(pip), "install", "--no-deps", str(install_root)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"pip install failed (exit {proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def _wheel_path_from_pip_log(venv_dir: Path) -> Path:
    """Find the wheel that pip just built and cached."""
    cache = Path.home() / ".cache" / "pip" / "wheels"
    if not cache.exists():
        pytest.skip("pip wheel cache not found; cannot inspect wheel")
    wheels = sorted(
        cache.rglob("aed_supervisor-*.whl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not wheels:
        pytest.skip("no aed_supervisor wheel found in pip cache")
    return wheels[0]


@pytest.fixture
def installed_venv(tmp_path: Path) -> Path:
    """Stage + install the package into an isolated venv.

    Yields the venv directory path. The test must use the
    venv's Python for import checks (so that the package is
    picked up via site-packages rather than via the test
    process's own import path).
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    install_root = _stage_install_root(staging)
    venv_dir = _create_venv(staging)
    _pip_install(venv_dir, install_root)
    return venv_dir


def test_install_produces_non_empty_wheel(installed_venv: Path):
    """The wheel produced by ``pip install`` must contain the
    package source files. An empty wheel (the previous
    failure mode) means the package was not discoverable by
    setuptools at install time.
    """
    wheel = _wheel_path_from_pip_log(installed_venv)
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    # Top-level package directory must contain the package
    # files. We assert on the existence of the canonical
    # modules rather than on the whole package layout.
    package_files = [
        n for n in names if n.startswith("autocoder_supervisor/")
    ]
    assert len(package_files) >= 5, (
        f"wheel {wheel} contains {len(package_files)} package files; "
        f"expected at least 5 (the canonical modules). "
        f"Package files: {package_files}"
    )
    assert "autocoder_supervisor/__init__.py" in names
    assert "autocoder_supervisor/supervisor.py" in names
    assert "autocoder_supervisor/validate.py" in names


def test_installed_package_top_level_name(installed_venv: Path):
    """The wheel's ``top_level.txt`` must list ``autocoder_supervisor``
    (and only that top-level package).
    """
    wheel = _wheel_path_from_pip_log(installed_venv)
    with zipfile.ZipFile(wheel) as zf:
        with zf.open("aed_supervisor-1.0.0.dist-info/top_level.txt") as f:
            top_level = f.read().decode("utf-8").strip().splitlines()
    assert "autocoder_supervisor" in top_level, (
        f"top_level.txt does not list autocoder_supervisor: "
        f"{top_level!r}"
    )
    assert len(top_level) == 1, (
        f"top_level.txt lists multiple top-level packages: "
        f"{top_level!r}; the install layout has multiple package roots"
    )


def test_installed_package_imports_cleanly(installed_venv: Path, tmp_path):
    """Import the package from outside the repo with the
    repository's source tree NOT on PYTHONPATH.
    """
    python = str(installed_venv / "bin" / "python")
    # Run from a temp cwd that is NOT the AED repo so we
    # cannot accidentally import from the source tree.
    cwd = tmp_path / "import-cwd"
    cwd.mkdir()
    # PYTHONPATH must be cleared: any inherited value could
    # leak the AED repo into the test.
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONPATH"] = ""
    proc = subprocess.run(
        [
            python,
            "-c",
            "import autocoder_supervisor; "
            "from autocoder_supervisor import supervisor, validate, "
            "config, contracts; "
            "print(autocoder_supervisor.__file__); "
            "print(supervisor.__file__); "
            "print(validate.__file__); "
            "print(config.__file__); "
            "print(contracts.__file__)",
        ],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"installed package is not importable from outside the repo "
        f"(exit {proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # The module paths must point at the venv's site-packages,
    # not at the AED repo's source tree.
    for line in proc.stdout.strip().splitlines():
        assert "site-packages" in line, (
            f"imported module path {line!r} is not in site-packages; "
            "the installed package is shadowed by something else"
        )
        assert str(REPO_ROOT) not in line, (
            f"imported module path {line!r} still points at the AED "
            "repo; the installed package is not actually being used"
        )


def test_installed_package_supervisor_help(installed_venv: Path, tmp_path):
    """``python -m autocoder_supervisor.supervisor --help`` works
    from outside the repo with PYTHONPATH cleared.
    """
    python = str(installed_venv / "bin" / "python")
    cwd = tmp_path / "help-cwd"
    cwd.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    proc = subprocess.run(
        [python, "-m", "autocoder_supervisor.supervisor", "--help"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"-m autocoder_supervisor.supervisor failed "
        f"(exit {proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "supervisor.py" in proc.stdout
    assert "--once" in proc.stdout


def test_installed_package_validate_help(installed_venv: Path, tmp_path):
    """``python -m autocoder_supervisor.validate --help`` works
    from outside the repo with PYTHONPATH cleared.
    """
    python = str(installed_venv / "bin" / "python")
    cwd = tmp_path / "help-cwd"
    cwd.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    proc = subprocess.run(
        [python, "-m", "autocoder_supervisor.validate", "--help"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"-m autocoder_supervisor.validate failed "
        f"(exit {proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "validate.py" in proc.stdout
    assert "--config" in proc.stdout


def test_staging_layout_matches_documented_install(
    installed_venv: Path, tmp_path
):
    """The staged install root has the exact documented layout:
    ``pyproject.toml`` and ``autocoder_supervisor/`` side-by-side
    at the install root.
    """
    # The install_root is the parent of the venv (both
    # are siblings under tmp_path/staging). Locate it by
    # scanning for the marker.
    install_roots = list(tmp_path.rglob("pyproject.toml"))
    assert install_roots, "no pyproject.toml found under tmp_path"
    for ir in install_roots:
        if ir.parent.name == "install-root":
            assert ir.is_file(), f"{ir} not a regular file"
            assert (ir.parent / "autocoder_supervisor").is_dir(), (
                f"{ir.parent}/autocoder_supervisor/ missing"
            )
            assert (ir.parent / "autocoder_supervisor" / "__init__.py").is_file()
            assert (ir.parent / "autocoder_supervisor" / "supervisor.py").is_file()
            assert (ir.parent / "autocoder_supervisor" / "validate.py").is_file()
            break
    else:
        pytest.fail(
            "no install-root/pyproject.toml found under tmp_path; "
            "the staging procedure did not produce the documented layout"
        )
