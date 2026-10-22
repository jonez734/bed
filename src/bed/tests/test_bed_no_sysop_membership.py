"""bed runs without sysop membership.

bed's functions check sysop through the zoid6-owned wrapper, not through role
membership, so bed does not need sysop. The install must never grant it.
"""

import importlib
import inspect
import re
from pathlib import Path

import bed

SQL_DIR = Path(bed.__file__).parent / "sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def test_no_bed_sql_grants_sysop_to_bed():
    for f in sorted(SQL_DIR.glob("bed_*.sql")):
        assert not re.search(r"GRANT\s+sysop\s+TO\s+bed\b", _code(f.read_text()), re.IGNORECASE), f.name


def test_install_does_not_grant_sysop_to_bed():
    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup)
    assert not re.search(r"GRANT\s+sysop\s+TO\s+bed", src, re.IGNORECASE)


def test_revoke_of_sysop_is_part_of_install():
    # The install revokes sysop from bed so an existing install is corrected.
    text = _code((SQL_DIR / "bed_service_grants.sql").read_text())
    assert re.search(r"REVOKE\s+sysop\s+FROM\s+bed\b", text, re.IGNORECASE)
