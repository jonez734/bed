"""bed sends messages through a zoid6-owned function that takes the token hash.

The sender is never a parameter. The function reads the moniker from the
engine.__bed_token row for that hash, and refuses the write if the row is
missing or expired. bed holds EXECUTE only, with no table privileges.

Slice 1 covers direct recipients. Groups, @here, @everyone, and named sources
are refused by this function until their slices land, so nothing is widened.
"""

import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_message_send.sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def _text() -> str:
    return _code(SQL.read_text())


def test_file_exists():
    assert SQL.exists(), f"missing {SQL}"


def test_send_function_signature_takes_hash_not_sender():
    text = _text()
    m = re.search(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.bed_message_send\s*\((.*?)\)\s*RETURNS", text, re.IGNORECASE | re.DOTALL)
    assert m, "bed_message_send not defined"
    params = m.group(1)
    assert "p_token_hash bytea" in params
    assert "sender" not in params.lower(), "sender must come from the session row, not a parameter"


def test_sender_read_from_session_row_and_expiry_enforced():
    text = _text()
    assert re.search(r"FROM\s+engine\.__bed_token", text, re.IGNORECASE)
    assert re.search(r"expires_at\s*>\s*now\(\)", text, re.IGNORECASE)


def test_refuses_missing_session():
    text = _text()
    assert re.search(r"RAISE\s+EXCEPTION.*session", text, re.IGNORECASE | re.DOTALL)


def test_slice_one_refuses_groups_and_broadcast():
    text = _text()
    assert re.search(r"@everyone|@here|group", text, re.IGNORECASE)
    assert re.search(r"RAISE\s+EXCEPTION.*not yet supported", text, re.IGNORECASE | re.DOTALL)


def test_security_definer_with_pinned_search_path():
    text = _text()
    assert "SECURITY DEFINER" in text
    assert re.search(r"SET\s+search_path\s*=\s*engine\s*,\s*pg_temp", text, re.IGNORECASE)


def test_owned_by_zoid6_and_execute_only_to_bed():
    text = _text()
    assert re.search(r"OWNER\s+TO\s+zoid6", text, re.IGNORECASE)
    assert re.search(r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+engine\.bed_message_send\(.*?\)\s+FROM\s+PUBLIC", text, re.IGNORECASE | re.DOTALL)
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_message_send\(.*?\)\s+TO\s+bed", text, re.IGNORECASE | re.DOTALL)


def test_install_runs_send_function():
    import importlib
    import inspect

    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup._ensure_bed_token_table)
    assert '"bed_message_send.sql"' in src
    assert src.index('"bed_message_read_functions.sql"') < src.index('"bed_message_send.sql"') < src.index('"bed_service_grants.sql"')


def test_blocked_recipients_are_dropped_in_sql():
    text = _text()
    assert re.search(r"FROM\s+engine\.__message_block", text, re.IGNORECASE)
    assert re.search(r"blocker_moniker\s*=\s*r\b|blocker_moniker\s*=\s*recipient", text, re.IGNORECASE)


def test_rate_limit_reserved_atomically_after_block_check():
    text = _text()
    assert re.search(r"INSERT\s+INTO\s+engine\.__message_rate_limit", text, re.IGNORECASE)
    assert re.search(r"WHERE\s+engine\.__message_rate_limit\.message_count\s*<", text, re.IGNORECASE)
    assert text.index("__message_block") < text.index("__message_rate_limit")


def test_refuses_when_rate_limit_reached():
    text = _text()
    assert re.search(r"RAISE\s+EXCEPTION.*rate", text, re.IGNORECASE | re.DOTALL)


def test_no_row_stored_when_every_recipient_blocked():
    text = _text()
    assert re.search(r"RAISE\s+EXCEPTION.*no recipients", text, re.IGNORECASE | re.DOTALL) or \
        re.search(r"RETURN\s+NULL", text, re.IGNORECASE)


def test_everyone_limited_to_sysop_and_expands_approved_members():
    text = _text()
    assert re.search(r"engine\.checkmemberflag\(\s*v_sender\s*,\s*'sysop'\s*\)", text, re.IGNORECASE)
    assert re.search(r"FROM\s+engine\.__member\s+\w+\s+WHERE\s+\w+\.approved\s*=\s*TRUE", text, re.IGNORECASE)


def test_named_group_requires_membership_or_sysop():
    text = _text()
    assert re.search(r"FROM\s+engine\.__message_group\s+\w+\s+WHERE\s+\w+\.name\s*=", text, re.IGNORECASE)
    assert re.search(r"FROM\s+engine\.__message_group_member", text, re.IGNORECASE)


def test_here_and_sources_remain_refused_in_sql():
    text = _text()
    assert re.search(r"RAISE\s+EXCEPTION\s+'bed_message_send:\s+@here is not yet supported", text, re.IGNORECASE)
    assert re.search(r"not yet supported", text, re.IGNORECASE)


def test_nested_groups_refused_not_silently_flattened():
    text = _text()
    assert re.search(r"nested", text, re.IGNORECASE)
