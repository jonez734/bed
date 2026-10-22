"""scripts/deploy_zoid6.sh pins wheels that exist, using the unique build-version format.

Every letteredolive build is named <prefix>YYYYMMDDHHMMSS (see scripts/next_version.sh in
the meta-repo). The deploy script must pin a wheel that is actually staged.
"""

import pathlib
import re

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "deploy_zoid6.sh"
WHEELS = pathlib.Path("/home/opencode/data/work/dist-wheels")


def _pin(name: str) -> str:
    m = re.search(rf'^{name}="([^"]+)"', SCRIPT.read_text(), re.MULTILINE)
    assert m, f"{name} not set in deploy_zoid6.sh"
    return m.group(1)


@pytest.mark.unit
def test_letteredolive_pin_uses_the_unique_timestamp_format():
    wheel = _pin("LO_WHEEL")
    assert re.match(r"letteredolive-0\.0\.2\.dev\d{14}-py3-none-any\.whl$", wheel), wheel


@pytest.mark.unit
def test_letteredolive_pin_names_a_staged_wheel():
    if not WHEELS.is_dir():
        pytest.skip("dist-wheels not present on this machine")
    assert (WHEELS / _pin("LO_WHEEL")).is_file()


@pytest.mark.unit
@pytest.mark.parametrize("name", ["BED_WHEEL", "BBS_WHEEL", "LO_WHEEL"])
def test_every_pin_names_a_staged_wheel(name):
    if not WHEELS.is_dir():
        pytest.skip("dist-wheels not present on this machine")
    assert (WHEELS / _pin(name)).is_file(), _pin(name)
