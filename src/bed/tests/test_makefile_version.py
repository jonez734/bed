"""bed's Makefile version targets use the shared unique build version.

Every project uses <prefix>YYYYMMDDHHMMSS, with the prefix 0.0.3.dev and the shared
scripts/next_version.sh in the meta-repo. A minute-resolution date stamp lets two builds
share a name, which is what these tests guard against. The deploy alias and the bbsengine6
build that deploy-venv runs first are also checked.
"""

import pathlib
import re

import pytest

MAKEFILE = pathlib.Path(__file__).resolve().parents[3] / "Makefile"


def _text():
    return MAKEFILE.read_text()


@pytest.mark.unit
def test_version_uses_the_shared_next_version_script():
    assert "next_version.sh" in _text()


@pytest.mark.unit
def test_version_is_not_minute_resolution():
    assert not re.search(r"VERSION\s*=\s*\$\(shell date \+%Y%m%d%H%M\)", _text())


@pytest.mark.unit
def test_version_prefix_is_0_0_2_dev():
    text = _text()
    assert "0.0.3.dev" in text
    assert "0.0.1.dev" not in text
    assert "0.0.2.dev" not in text


@pytest.mark.unit
def test_deploy_is_an_alias_for_deploy_venv():
    assert re.search(r"^deploy:\s*deploy-venv\s*$", _text(), re.MULTILINE)


@pytest.mark.unit
def test_deploy_venv_builds_bbsengine6_before_bed():
    text = _text()
    deploy_venv = text[text.index("deploy-venv: clean"):]
    assert deploy_venv.index("$(BBSENGINE_DIR)") < deploy_venv.index("$(CURDIR)")


def _deploy_venv_block():
    text = _text()
    return text[text.index("deploy-venv: clean"):text.index("# Umbrella prod install")]


@pytest.mark.unit
def test_deploy_venv_installs_into_the_service_venv_by_default():
    block = _deploy_venv_block()
    assert "$(VENV_DIR)/bin/pip install" in block
    assert "$(VIRTUAL_ENV)/bin/pip" not in block


@pytest.mark.unit
def test_deploy_venv_does_not_use_sudo():
    # jam is in group bed and /var/lib/bed/venv is group-writable, so deploy needs no sudo.
    assert "sudo" not in _deploy_venv_block()


@pytest.mark.unit
def test_venv_dir_is_the_single_source_of_truth():
    # install and deploy-venv must agree on the venv path: one VENV_DIR definition. It takes
    # deploytool's DEPLOY_PREFIX, then the active venv, then $(BED_HOME)/venv.
    assert re.search(
        r"^VENV_DIR\s*\?=\s*\$\(or \$\(DEPLOY_PREFIX\),\$\(VIRTUAL_ENV\),\$\(BED_HOME\)/venv\)\s*$",
        _text(),
        re.MULTILINE,
    )
