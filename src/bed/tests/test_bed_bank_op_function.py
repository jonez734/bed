"""engine.bed_bank_op: one SECURITY DEFINER function for the bank writes and reads bed needs.

It takes the token digest, an op name and a jsonb argument object, like bbsengine6.module.access
takes an op. The actor and sysop status come from the verified token row, never from the message.
Each op checks its own rule, and an unknown op is refused. bed is the only role granted execute.
"""

import re
from pathlib import Path

import pytest

SQL = Path(__file__).resolve().parents[1] / "sql" / "bed_bank_op_functions.sql"

OPS = ["add", "remove", "history", "list_all", "pending",
       "transfer_request", "transfer_approve", "transfer_reject"]


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


@pytest.mark.unit
def test_file_exists():
    assert SQL.is_file(), SQL


@pytest.mark.unit
def test_signature_takes_token_digest_op_and_jsonb_args():
    t = _code(SQL.read_text())
    m = re.search(r"FUNCTION\s+engine\.bed_bank_op\s*\((.*?)\)\s*RETURNS", t, re.I | re.S)
    assert m, "bed_bank_op not defined"
    args = m.group(1).lower()
    assert "p_token_hash bytea" in args and "p_op text" in args and "p_args jsonb" in args


@pytest.mark.unit
def test_actor_comes_from_the_verified_token_row():
    t = _code(SQL.read_text()).lower()
    assert "from engine.__bed_token" in t
    assert "expires_at > now()" in t
    assert "no live session" in t


@pytest.mark.unit
def test_every_op_is_dispatched():
    t = _code(SQL.read_text())
    for op in OPS:
        assert f"'{op}'" in t, f"op {op} not handled"


@pytest.mark.unit
def test_unknown_op_is_refused():
    t = _code(SQL.read_text()).lower()
    assert "unknown op" in t or "unknown bank op" in t


@pytest.mark.unit
def test_security_definer_owned_by_zoid6_and_search_path_pinned():
    t = _code(SQL.read_text()).lower()
    assert "security definer" in t
    assert "set search_path" in t
    assert "owner to zoid6" in t


@pytest.mark.unit
def test_execute_granted_to_bed_only():
    t = _code(SQL.read_text())
    assert re.search(r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+engine\.bed_bank_op\(.*?\)\s+FROM\s+PUBLIC", t, re.I | re.S)
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_bank_op\(.*?\)\s+TO\s+bed", t, re.I | re.S)
    assert not re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_bank_op\(.*?\)\s+TO\s+member", t, re.I | re.S)
