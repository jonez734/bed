"""bed must not hold install-only privileges at runtime.

REFERENCES on engine.__member and CREATE on the database are needed only by
the owner-run install (bed --install, run as jam). The grants file must
revoke them from bed so the service role keeps runtime access only.
"""

import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_service_grants.sql"


def _text() -> str:
    return "\n".join(
        line for line in SQL.read_text().splitlines()
        if not line.lstrip().startswith("--")
    )


def test_revokes_references_on_member_table_from_bed():
    assert re.search(
        r"REVOKE\s+REFERENCES\s+ON\s+engine\.__member\s+FROM\s+bed\b",
        _text(),
        re.IGNORECASE,
    )


def test_revokes_create_on_database_from_bed():
    # Database name is resolved with current_database(), not hard-coded.
    assert re.search(
        r"REVOKE\s+CREATE\s+ON\s+DATABASE\s+%I\s+FROM\s+bed\b",
        _text(),
        re.IGNORECASE,
    )
    assert "current_database()" in _text()


def test_keeps_runtime_select_grant():
    assert re.search(
        r"GRANT\s+SELECT\s+ON\s+engine\.pgrole\s+TO\s+bed\b",
        _text(),
        re.IGNORECASE,
    )
