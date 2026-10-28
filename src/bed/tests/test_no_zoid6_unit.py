"""bed owns no zoid6 unit or install path.

zoid6 runs bed's daemon with zoid6's router, but its systemd unit belongs to zoid6
(zoid6/src/zoid6/daemon/zoid6.service) and zoid6's install-systemd installs it. These
tests fail if a zoid6 unit, or a target that installs one, reappears in bed.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
DAEMON = ROOT / "src" / "bed" / "daemon"
MAKEFILES = [ROOT / "Makefile", ROOT / "src" / "Makefile"]


def _strip_comments(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.unit
def test_bed_daemon_dir_has_no_zoid6_unit():
    assert not (DAEMON / "zoid6.service").exists()
    assert not list(DAEMON.glob("zoid6*.service"))


@pytest.mark.unit
@pytest.mark.parametrize("path", MAKEFILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_makefiles_install_no_zoid6_unit(path):
    text = _strip_comments(path.read_text())
    assert "install-zoid6" not in text
    assert "uninstall-zoid6" not in text
    assert not re.search(r"^ZOID6_UNIT", text, re.M)
    assert "daemon/zoid6.service" not in text


@pytest.mark.unit
def test_no_zoid6_unit_file_in_the_package():
    # bed's package data must not ship a zoid6 unit either.
    pkg = ROOT / "src" / "bed"
    assert not list(pkg.rglob("zoid6*.service"))
