"""bed's venv path can be set by deploytool's --prefix (DEPLOY_PREFIX), like letteredolive.

Without it, `deploy --prefix` is silently ignored for bed. The default is
/home/bed/venv (the bed account's venv).
"""

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _venv_dir(env_extra):
    """Ask make what VENV_DIR resolves to, without running any recipe."""
    env = {k: v for k, v in os.environ.items() if k != "DEPLOY_PREFIX"}
    env.update(env_extra)
    out = subprocess.run(
        ["make", "-s", "-C", str(ROOT), "-f", str(ROOT / "Makefile"), "print-venv-dir"],
        capture_output=True, text=True, env=env,
    )
    return out


@pytest.mark.unit
def test_default_venv_dir_is_bed_account_venv():
    if shutil.which("make") is None:
        pytest.skip("make not available")
    r = _venv_dir({})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/home/bed/venv", r.stdout


@pytest.mark.unit
def test_deploy_prefix_sets_the_venv_dir():
    if shutil.which("make") is None:
        pytest.skip("make not available")
    r = _venv_dir({"DEPLOY_PREFIX": "/var/lib/custom/venv"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "/var/lib/custom/venv", r.stdout
