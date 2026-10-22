"""bed reads a bank balance through a zoid6-owned SECURITY DEFINER function.

bed holds no table privileges on bank.__account. bed_bank_balance takes the
token hash, reads the acting moniker from engine.__bed_token, and allows the
read only for the account owner or a sysop, as bbsengine6.bank.access does.
"""

import importlib
import inspect
import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_bank_functions.sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def _text() -> str:
    return _code(SQL.read_text())


def test_file_exists():
    assert SQL.exists(), f"missing {SQL}"


def test_balance_function_signature_takes_hash_and_moniker():
    m = re.search(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.bed_bank_balance\s*\((.*?)\)\s*RETURNS",
                  _text(), re.IGNORECASE | re.DOTALL)
    assert m, "bed_bank_balance not defined"
    assert "p_token_hash bytea" in m.group(1)
    assert "p_moniker citext" in m.group(1)


def test_balance_returns_existence_and_balance():
    t = _text()
    assert re.search(r"RETURNS\s+TABLE\s*\(\s*account_exists\s+boolean\s*,\s*balance\s+numeric",
                     t, re.IGNORECASE)


def test_balance_enforces_owner_or_sysop():
    t = _text()
    assert "engine.bed_is_sysop(" in t
    assert re.search(r"forbidden", t, re.IGNORECASE)


def test_balance_reads_acting_moniker_from_session_row():
    t = _text()
    assert re.search(r"FROM\s+engine\.__bed_token", t, re.IGNORECASE)
    assert re.search(r"expires_at\s*>\s*now\(\)", t, re.IGNORECASE)


def test_security_definer_with_fixed_search_path():
    t = _text()
    assert "SECURITY DEFINER" in t
    assert re.search(r"SET\s+search_path\s*=\s*engine\s*,\s*public\s*,\s*pg_temp", t, re.IGNORECASE)


def test_owned_by_zoid6_and_execute_only_to_bed():
    t = _text()
    assert re.search(r"OWNER\s+TO\s+zoid6", t, re.IGNORECASE)
    assert re.search(r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+engine\.bed_bank_balance\(.*?\)\s+FROM\s+PUBLIC", t, re.IGNORECASE | re.DOTALL)
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_bank_balance\(.*?\)\s+TO\s+bed", t, re.IGNORECASE | re.DOTALL)


def test_balance_function_is_read_only():
    assert not re.search(r"\b(INSERT|UPDATE|DELETE)\b", _text(), re.IGNORECASE)


def test_handle_balance_uses_the_function_not_bankservice():
    bank_mod = importlib.import_module("bed.api.bank")
    src = inspect.getsource(bank_mod.BankService._handle_balance)
    assert "bed_bank_balance" in src
    assert "account.get(" not in src


def test_install_runs_bank_functions_before_grants():
    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup._ensure_bed_token_table)
    assert '"bed_bank_functions.sql"' in src
    assert src.index('"bed_bank_functions.sql"') < src.index('"bed_service_grants.sql"')
