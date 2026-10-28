"""bed reads nothing from /etc, so it must not install to or read from /etc.

A read-only root filesystem makes any /etc write fail, and a daemon that reads
/etc breaks there. These tests pin the rule for the runtime, the install
templates, and the Makefiles. The configuration is the packaged bed.json.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
DAEMON = ROOT / "src" / "bed" / "daemon"
MAKEFILES = [ROOT / "Makefile", ROOT / "src" / "Makefile"]
RUNTIME_SOURCES = [p for p in (ROOT / "src" / "bed").rglob("*.py") if "tests" not in p.parts]

ETC_PATH = re.compile(r"""(?<![\w.])/etc/""")

# Messages that name an OS file, not a bed config or install path. Not reads.
ALLOWED_MESSAGES = ("/etc/hosts",)


def _strip_comments(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.unit
@pytest.mark.parametrize("path", sorted(p for p in DAEMON.glob("*") if p.is_file()), ids=lambda p: p.name)
def test_daemon_templates_do_not_reference_etc(path):
    text = _strip_comments(path.read_text())
    assert not ETC_PATH.search(text), f"{path.name} references /etc"


@pytest.mark.unit
@pytest.mark.parametrize("path", MAKEFILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_makefiles_install_nothing_into_etc(path):
    text = _strip_comments(path.read_text())
    # ETC_DIR / FACTORY_DIR / install-etc were the only /etc writers; none may return.
    assert "install-etc" not in text
    assert not re.search(r"^\s*(ETC_DIR|ZOID6_ETC_DIR|FACTORY_DIR|ZOID6_FACTORY)\b", text, re.M)
    assert not re.search(r"\$\((ETC_DIR|ZOID6_ETC_DIR)\)", text)


@pytest.mark.unit
def test_runtime_code_does_not_read_etc():
    offenders = []
    for path in RUNTIME_SOURCES:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if ETC_PATH.search(code) and not any(m in code for m in ALLOWED_MESSAGES):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "runtime code reads /etc:\n" + "\n".join(offenders)


@pytest.mark.unit
def test_bed_home_is_a_single_makefile_variable():
    """BED_HOME is the one place the account home is set; /var/lib/bed is not hard-coded."""
    top = _strip_comments((ROOT / "Makefile").read_text())
    assert re.search(r"^BED_HOME\s*\?=", top, re.M)
    for path in MAKEFILES:
        assert "/var/lib/bed" not in _strip_comments(path.read_text()), path
    for name in ("bed.service", "bed.sysusers", "bed.tmpfiles"):
        assert "/var/lib/bed" not in (DAEMON / name).read_text(), name


@pytest.mark.unit
def test_install_venv_gates_on_packaged_configuration():
    """install-venv must refuse a wheel without the packaged bed.json."""
    text = (ROOT / "Makefile").read_text()
    assert "CHECK_CONFIGURED" in text
    assert re.search(r"\$\(call CHECK_CONFIGURED", text), "CHECK_CONFIGURED is defined but never called"
    assert "bed/data/bed.json" in text


@pytest.mark.unit
def test_packaged_configuration_ships_in_source():
    assert (ROOT / "src" / "bed" / "data" / "bed.json").is_file()
