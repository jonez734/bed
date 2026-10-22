"""The shipped bed.json names the unconfigured sentinel database, not 'bed'.

'bed' and 'NEEDINFO' are reserved (bbsengine6 refuses to create them). The
packaged default must not point at a real-looking database, so a sysop has
to set the name before anything can work.
"""

import json
from pathlib import Path

import bed
import pytest


def _shipped():
    return json.loads((Path(bed.__file__).parent / "data" / "bed.json").read_text())


@pytest.mark.unit
def test_shipped_dsn_names_needinfo():
    dsn = _shipped()["database"]["dsn"]
    assert "dbname=NEEDINFO" in dsn


@pytest.mark.unit
def test_shipped_dsn_does_not_name_bed():
    dsn = _shipped()["database"]["dsn"]
    assert "dbname=bed" not in dsn


@pytest.mark.unit
def test_shipped_bed_name_identity_is_unchanged():
    # "bed" as the bed's own name (logging, identity) is not a database name.
    assert _shipped()["bed"]["name"] == "bed"


FACTORY_JSON = Path(bed.__file__).parents[2] / "usr" / "share" / "factory" / "etc" / "bed" / "bed.json"


@pytest.mark.unit
def test_factory_default_dsn_names_needinfo():
    # usr/share/factory/etc/bed/bed.json is installed to /etc/bed by `make install-etc`.
    dsn = json.loads(FACTORY_JSON.read_text())["database"]["dsn"]
    assert "dbname=NEEDINFO" in dsn
    assert "dbname=bed" not in dsn
