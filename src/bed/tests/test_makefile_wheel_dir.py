"""WHEEL_DIR must expand to a real per-user directory under /tmp.

`$$` in a Makefile variable becomes a literal `$`, so /tmp/bed-$ was created instead of a per-user
directory. The directory was owned by the account that first ran the deploy, so the next user's
rm could not clear it, and the deploy failed with Permission denied.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _wheel_dir() -> str:
    out = subprocess.run(
        ["make", "-s", "-C", str(ROOT), "print-wheel-dir"],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.mark.unit
def test_wheel_dir_has_no_literal_dollar_sign():
    assert "$" not in _wheel_dir()


@pytest.mark.unit
def test_wheel_dir_is_under_tmp_and_names_the_user():
    d = _wheel_dir()
    assert d.startswith("/tmp/"), d
    assert os.environ.get("USER") or subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip() in d
