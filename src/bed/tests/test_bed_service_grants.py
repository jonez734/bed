"""bed_service_grants.sql must not grant bed standing membership in any member role.

bed must not act as other roles (no membership in jam, the superuser, or
any per-member role). Membership grants are not standing policy.
"""

import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_service_grants.sql"


def _statements() -> list[str]:
    text = "\n".join(
        line for line in SQL.read_text().splitlines()
        if not line.lstrip().startswith("--")
    )
    return [s.strip() for s in text.split(";") if s.strip()]


def test_no_membership_grant_to_bed():
    for stmt in _statements():
        # "GRANT <role> TO bed" where <role> is not a privilege keyword
        m = re.match(r"GRANT\s+(\w+)\s+TO\s+bed\b", stmt, re.IGNORECASE)
        if m:
            assert m.group(1).upper() in {"SELECT", "USAGE", "CREATE", "REFERENCES", "INSERT", "UPDATE", "DELETE", "EXECUTE"}, (
                f"standing membership grant to bed: {stmt!r}"
            )


def test_no_grant_of_jam_to_bed():
    assert not any(re.search(r"\bjam\b", s) and "TO bed" in s for s in _statements())
