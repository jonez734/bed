"""bed writes casino tables through a zoid6-owned SECURITY DEFINER function.

bed holds no table privileges on the casino tables. engine.bed_casino_op takes the
token hash, resolves the acting moniker from engine.__bed_token, and dispatches by op
(create, update, join, leave). Ownership and sysop rules are enforced in the function.

These tests pin the contract in source. Behaviour was checked against a database in
rolled-back transactions (see the commit message).
"""

import inspect
from pathlib import Path

import bed
from bed import startup

SQL = Path(bed.__file__).parent / "sql" / "bed_casino_op_functions.sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def _text() -> str:
    return _code(SQL.read_text())


def test_function_is_security_definer_owned_by_zoid6():
    t = _text()
    assert "SECURITY DEFINER" in t
    assert "OWNER TO zoid6" in t


def test_bed_is_the_only_grantee():
    t = _text()
    assert "REVOKE ALL ON FUNCTION engine.bed_casino_op(bytea, text, jsonb) FROM PUBLIC" in t
    assert "GRANT EXECUTE ON FUNCTION engine.bed_casino_op(bytea, text, jsonb) TO bed" in t


def test_actor_comes_from_the_token_row():
    t = _text()
    assert "FROM engine.__bed_token t" in t
    assert "t.token_hash = p_token_hash" in t


def test_every_write_op_is_dispatched():
    t = _text()
    for op in ("create", "update", "join", "leave"):
        assert f"p_op = '{op}'" in t, op


def test_update_enforces_owner_or_sysop_and_validates_status_and_hidden():
    t = _text()
    assert "IF NOT v_sysop AND v_actor IS DISTINCT FROM v_owner" in t
    assert "('open', 'closed')" in t
    assert "jsonb_typeof(p_args->'hidden') <> 'boolean'" in t


def test_leave_lets_only_the_player_or_a_sysop_remove_a_seat():
    t = _text()
    assert "IF NOT v_sysop AND v_actor IS DISTINCT FROM v_target" in t


def test_unknown_op_is_refused():
    assert "unknown op %" in _text()


def test_every_refusal_names_the_failing_op():
    t = _text()
    assert "bed_casino_op(%): forbidden', p_op" in t
    assert "bed_casino_op(%): no live session for this token', p_op" in t


def test_install_loads_the_casino_op_function_after_read():
    src = inspect.getsource(startup._ensure_bed_token_table)
    assert "bed_casino_op_functions.sql" in src
    assert src.index("bed_casino_read_functions.sql") < src.index("bed_casino_op_functions.sql")
