"""bed's identd target: install-identd is real, install-pgsql is its alias, and
BED_DATABASENAME is required (no default, NEEDINFO and malformed values refused).

Runs make against bed's own Makefile with an unreachable PGSQL_DATA, so no
root, no database, and no pg_hba change happens. Each refusal must happen before
the first file operation.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]  # bed/src
MAKEFILE = SRC / "Makefile"

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")


def _make(*args):
    return subprocess.run(
        ["make", "-C", str(SRC), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "PGSQL_DATA": "/nonexistent-bed-test"},
    )


def test_install_identd_is_the_real_target():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert re.search(r"^install-identd:\s*$", text, re.M)
    assert re.search(r"^install-pgsql:\s*install-identd\s*$", text, re.M)


def test_pgsql_anchor_uses_the_variable_not_a_literal_zoid6():
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "($(BED_DATABASENAME))" in text
    recipe = text.split("install-identd:", 1)[1].split("\ninstall-", 1)[0]
    assert "(zoid6)" not in recipe


def test_database_name_has_no_default_sentinel_is_refused():
    proc = _make("install-identd", "BED_DATABASENAME=NEEDINFO")
    assert proc.returncode != 0
    assert "BED_DATABASENAME" in proc.stderr
    assert "NEEDINFO is refused" in proc.stderr


def test_empty_database_name_is_refused():
    proc = _make("install-identd", "BED_DATABASENAME=")
    assert proc.returncode != 0
    assert "set BED_DATABASENAME" in proc.stderr


@pytest.mark.parametrize("bad", ["bad name", "Zoid6", "zoid6;rm", "a.b"])
def test_malformed_database_name_is_refused(bad):
    proc = _make("install-identd", f"BED_DATABASENAME={bad}")
    assert proc.returncode != 0
    assert "must match [a-z0-9_]+" in proc.stderr


def test_alias_runs_the_same_guard():
    proc = _make("install-pgsql", "BED_DATABASENAME=NEEDINFO")
    assert proc.returncode != 0
    assert "NEEDINFO is refused" in proc.stderr


def test_refusal_happens_before_any_file_operation(tmp_path):
    """A refused run must not back up or edit pg_hba.conf. PGSQL_DATA points at a
    directory that holds a pg_hba.conf; it must come through unchanged."""
    hba = tmp_path / "pg_hba.conf"
    hba.write_text("host zoid6 all 127.0.0.1/32 md5\n", encoding="utf-8")
    before = hba.read_text(encoding="utf-8")
    proc = subprocess.run(
        ["make", "-C", str(SRC), "install-identd", "BED_DATABASENAME=NEEDINFO",
         f"PGSQL_DATA={tmp_path}"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode != 0
    assert hba.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("*.bak-*"))
