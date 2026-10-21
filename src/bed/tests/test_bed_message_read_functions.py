"""bed reads message rows through zoid6-owned SECURITY DEFINER functions.

bed holds no table privileges on the message tables. It gets EXECUTE on two
read functions only. The functions run with zoid6's rights and a fixed
search_path, so they cannot be redirected by the caller.
"""

import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_message_read_functions.sql"


def _text() -> str:
    return "\n".join(
        line for line in SQL.read_text().splitlines()
        if not line.lstrip().startswith("--")
    )


def test_file_exists():
    assert SQL.exists(), f"missing {SQL}"


def test_pending_page_function_signature():
    assert re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.bed_list_pending_page\s*\(\s*"
        r"recipient\s+citext\s*,\s*lim\s+int\s*,\s*before_datestamp\s+timestamptz\s+DEFAULT\s+NULL\s*,\s*before_id\s+bigint\s+DEFAULT\s+NULL\s*,\s*max_age_days\s+int\s+DEFAULT\s+0\s*\)",
        _text(),
        re.IGNORECASE,
    ), "bed_list_pending_page must take (recipient, lim, before_datestamp, before_id, max_age_days int DEFAULT 0)"


def test_pending_page_applies_age_limit_in_function():
    text = _text()
    assert re.search(
        r"max_age_days\s*>\s*0\s+AND\s+m\.datestamp\s*>=\s*now\(\)\s*-\s*\(max_age_days\s*\|\|\s*' days'\)::interval",
        text,
        re.IGNORECASE,
    ) or re.search(r"make_interval\s*\(\s*days\s*=>\s*max_age_days", text, re.IGNORECASE)


def test_count_unread_function_signature():
    assert re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.bed_count_unread\s*\(\s*recipient\s+citext\s*\)",
        _text(),
        re.IGNORECASE,
    )


def test_both_functions_are_security_definer_with_fixed_search_path():
    text = _text()
    assert len(re.findall(r"SECURITY\s+DEFINER", text, re.IGNORECASE)) == 2
    assert len(re.findall(r"SET\s+search_path\s*=\s*engine\s*,\s*pg_temp", text, re.IGNORECASE)) == 2


def test_old_four_argument_overload_is_dropped():
    # CREATE OR REPLACE does not remove an overload. The four-argument form
    # predates the max_age_days TTL and must not stay executable by bed.
    text = _text()
    assert re.search(
        r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+engine\.bed_list_pending_page\s*\(\s*citext\s*,\s*int\s*,\s*timestamptz\s*,\s*bigint\s*\)",
        text,
        re.IGNORECASE,
    )


def test_functions_read_only_no_writes():
    text = _text()
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b", text, re.IGNORECASE)


def test_execute_granted_to_bed_only_and_revoked_from_public():
    text = _text()
    assert re.search(r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+engine\.bed_list_pending_page\(.*\)\s+FROM\s+PUBLIC", text, re.IGNORECASE | re.DOTALL)
    assert re.search(r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+engine\.bed_count_unread\(.*\)\s+FROM\s+PUBLIC", text, re.IGNORECASE | re.DOTALL)
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_list_pending_page\(.*\)\s+TO\s+bed", text, re.IGNORECASE | re.DOTALL)
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_count_unread\(.*\)\s+TO\s+bed", text, re.IGNORECASE | re.DOTALL)


def test_owned_by_zoid6():
    text = _text()
    # Functions are created by the owner role, so the file must not hand them to bed.
    assert not re.search(r"OWNER\s+TO\s+bed\b", text, re.IGNORECASE)
    assert "SET ROLE" not in text.upper()
