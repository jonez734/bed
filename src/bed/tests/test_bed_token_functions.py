"""bed reads and writes bearer tokens only through zoid6-owned functions.

bed holds no table privileges on engine.__bed_token. It gets EXECUTE on
three functions (put, get, delete). Each takes the SHA-256 digest of the
token, never the token, and runs with zoid6's rights under a fixed search_path.
"""

import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_token_functions.sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def _text() -> str:
    return _code(SQL.read_text())


def test_file_exists():
    assert SQL.exists(), f"missing {SQL}"


def test_three_functions_defined():
    text = _text()
    for name in ("bed_token_put", "bed_token_get", "bed_token_delete"):
        assert re.search(rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.{name}\(", text, re.IGNORECASE), name


def test_functions_take_hash_not_token():
    text = _text()
    assert re.search(r"bed_token_get\s*\(\s*p_token_hash\s+bytea\s*\)", text, re.IGNORECASE)
    assert re.search(r"bed_token_delete\s*\(\s*p_token_hash\s+bytea\s*\)", text, re.IGNORECASE)
    assert not re.search(r"p_token\s+text", text, re.IGNORECASE)


def test_security_definer_with_pinned_search_path():
    text = _text()
    assert len(re.findall(r"SECURITY\s+DEFINER", text, re.IGNORECASE)) == 4
    assert len(re.findall(r"SET\s+search_path\s*=\s*engine\s*,\s*pg_temp", text, re.IGNORECASE)) == 4


def test_functions_owned_by_zoid6():
    text = _text()
    for name in ("bed_token_put", "bed_token_get", "bed_token_delete"):
        assert re.search(rf"ALTER\s+FUNCTION\s+engine\.{name}\(.*?\)\s+OWNER\s+TO\s+zoid6", text, re.IGNORECASE | re.DOTALL), name


def test_execute_granted_to_bed_only_and_public_revoked():
    text = _text()
    for name in ("bed_token_put", "bed_token_get", "bed_token_delete"):
        assert re.search(rf"REVOKE\s+ALL\s+ON\s+FUNCTION\s+engine\.{name}\(.*?\)\s+FROM\s+PUBLIC", text, re.IGNORECASE | re.DOTALL), name
        assert re.search(rf"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.{name}\(.*?\)\s+TO\s+bed", text, re.IGNORECASE | re.DOTALL), name


def test_bed_has_no_table_grant_on_token_table():
    assert not re.search(r"GRANT\s+.*ON\s+engine\.__bed_token\s+TO\s+bed", _text(), re.IGNORECASE)


def test_put_function_is_upsert_on_hash():
    text = _text()
    assert "ON CONFLICT (token_hash)" in text


def test_gc_function_defined_and_granted():
    text = _text()
    assert re.search(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.bed_token_gc\(", text, re.IGNORECASE)
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_token_gc\(.*?\)\s+TO\s+bed", text, re.IGNORECASE | re.DOTALL)
    assert re.search(r"OWNER\s+TO\s+zoid6", text, re.IGNORECASE)


def test_store_gc_calls_function_not_table():
    from bed.store import token_store as s
    sql, _ = s.gc_sql(None)
    assert "__bed_token" not in sql
    assert "engine.bed_token_gc" in sql
