"""engine.__bed_token stores a SHA-256 hash of the bearer token, never the token.

The table is created by ``bed --install`` as the owner (zoid6). bed holds no
table privileges and the token store runs no DDL at runtime.
"""

import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_token.sql"


def _code_lines(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def test_schema_file_exists_in_bed_sql():
    assert SQL.exists(), f"missing {SQL}"


def test_token_column_is_sha256_hash_not_raw_text():
    text = _code_lines(SQL.read_text())
    assert re.search(r"token_hash\s+bytea\s+PRIMARY\s+KEY", text, re.IGNORECASE)
    assert not re.search(r"^\s*token\s+text", text, re.IGNORECASE | re.MULTILINE)


def test_no_raw_bearer_column_anywhere():
    text = _code_lines(SQL.read_text())
    assert not re.search(r"\btoken\s+text\b", text, re.IGNORECASE)


def test_table_is_created_by_install_not_lazily():
    # The lazy copy under data/sql must be gone; install owns the table.
    assert not (Path(bed.__file__).parent / "data" / "sql" / "bed_token.sql").exists()


def test_table_owned_by_zoid6():
    text = _code_lines(SQL.read_text())
    assert re.search(r"ALTER\s+TABLE\s+engine\.__bed_token\s+OWNER\s+TO\s+zoid6", text, re.IGNORECASE)


def test_bed_gets_no_table_privileges():
    text = _code_lines(SQL.read_text())
    assert not re.search(r"GRANT\s+.*\s+ON\s+engine\.__bed_token\s+TO\s+bed", text, re.IGNORECASE)


def test_store_runs_no_ddl_at_runtime():
    from bed.api import token_store

    src = Path(token_store.__file__).read_text()
    assert "CREATE TABLE" not in src
    assert "CREATE INDEX" not in src
