"""bed's CasinoService serves casino table ops through engine.bed_casino_read / bed_casino_op.

Each WebSocket type maps to one database function and one op name. The service passes only the
SHA-256 digest of the bearer token, the op name, and the message's arguments. The acting moniker
is never taken from the request. Session and token checks are the bank service's; the bank
authorisation rule is not applied, because casino's rules live in the database functions.
"""

import asyncio
import argparse
import inspect
import json
from unittest.mock import MagicMock

import pytest

from bed.api.casino import CasinoService, _TYPE_TO_CALL
from bed.store.token_store import token_hash

EXPECTED = {
    "casino_list_tables": ("bed_casino_read", "list_tables"),
    "casino_get_table": ("bed_casino_read", "get_table"),
    "casino_create_table": ("bed_casino_op", "create"),
    "casino_update_table": ("bed_casino_op", "update"),
    "casino_join_table": ("bed_casino_op", "join"),
    "casino_leave_table": ("bed_casino_op", "leave"),
}


class _Cursor:
    def __init__(self, result):
        self.result = result
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return (self.result,)


class _Conn:
    def __init__(self, result):
        self.cur = _Cursor(result)

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _svc(result=None):
    svc = CasinoService(MagicMock(), MagicMock())
    conn = _Conn(result)
    svc._conn = lambda: conn
    return svc, conn


def _state(token="tok-abc"):
    st = MagicMock()
    st.auth_service_token = token
    return st


@pytest.mark.unit
def test_type_map_has_one_function_and_op_per_message():
    assert _TYPE_TO_CALL == EXPECTED


@pytest.mark.unit
def test_handled_types_are_the_six_casino_messages():
    assert set(CasinoService.HANDLED_TYPES) == set(EXPECTED)


@pytest.mark.unit
def test_call_passes_token_digest_op_and_args_not_moniker():
    svc, conn = _svc({"success": True, "tables": []})
    out = svc._call("bed_casino_read", "list_tables", "tok-abc", {"game_type": "blackjack"}, "casino_list_tables")
    sql, params = conn.cur.executed[0]
    assert "engine.bed_casino_read(%s, %s, %s::jsonb)" in sql
    digest, op, args = params
    assert digest == token_hash("tok-abc")
    assert op == "list_tables"
    assert json.loads(args) == {"game_type": "blackjack"}
    assert out == {"type": "casino_list_tables", "success": True, "tables": []}


@pytest.mark.unit
def test_call_sends_no_plaintext_token_to_the_database():
    svc, conn = _svc({"success": True})
    svc._call("bed_casino_op", "create", "tok-secret", {"moniker": "t"}, "casino_create_table")
    _, params = conn.cur.executed[0]
    assert "tok-secret" not in json.dumps([str(p) for p in params])


@pytest.mark.unit
def test_result_from_database_is_returned_with_the_message_type():
    svc, _ = _svc({"success": False, "message": "Status must be open or closed"})
    out = svc._call("bed_casino_op", "update", "tok", {}, "casino_update_table")
    assert out == {"type": "casino_update_table", "success": False, "message": "Status must be open or closed"}


@pytest.mark.unit
def test_string_jsonb_result_is_decoded():
    svc, _ = _svc(json.dumps({"success": True, "table": {"moniker": "t1"}}))
    out = svc._call("bed_casino_op", "create", "tok", {}, "casino_create_table")
    assert out["table"] == {"moniker": "t1"}


@pytest.mark.unit
def test_database_error_becomes_an_error_envelope_with_the_op_name():
    svc = CasinoService(MagicMock(), MagicMock())

    class _Boom:
        def __enter__(self):
            raise RuntimeError("permission denied for schema engine")

        def __exit__(self, *a):
            return False

    svc._conn = lambda: _Boom()
    out = svc._call("bed_casino_op", "join", "tok", {}, "casino_join_table")
    assert "join failed" in json.dumps(out)
    assert "permission denied" in json.dumps(out)


@pytest.mark.unit
def test_handle_message_ignores_unknown_types():
    svc, _ = _svc({"success": True})
    result = asyncio.run(svc.handle_message(None, MagicMock(), "/", {"type": "bank_balance"}))
    assert result is None


@pytest.mark.unit
def test_handle_message_refuses_when_gate_refuses(monkeypatch):
    svc, conn = _svc({"success": True})
    refused = {"type": "error", "code": "unauthorized"}
    monkeypatch.setattr(svc, "_check_access", lambda ws, op, msg: (None, refused))
    result = asyncio.run(svc.handle_message(None, MagicMock(), "/", {"type": "casino_create_table"}))
    assert result is refused
    assert conn.cur.executed == [], "a refused op must not reach the database"


@pytest.mark.unit
def test_handle_message_passes_the_session_token_to_the_database(monkeypatch):
    svc, conn = _svc({"success": True, "tables": []})
    monkeypatch.setattr(svc, "_check_access", lambda ws, op, msg: (_state("sess-tok"), None))
    asyncio.run(svc.handle_message(None, MagicMock(), "/", {"type": "casino_list_tables", "game_type": "blackjack"}))
    _, params = conn.cur.executed[0]
    assert params[0] == token_hash("sess-tok")
    assert params[1] == "list_tables"


@pytest.mark.unit
def test_gate_skips_the_bank_authorisation_rule():
    # Check the code, not the docstring: the docstring names the rule it skips.
    code = "\n".join(
        l for l in inspect.getsource(CasinoService._check_access).splitlines()
        if not l.lstrip().startswith(("#", '"""')) and '"""' not in l
    )
    assert "_bank_access(" not in code
    assert "from bbsengine6.bank" not in code and "bbsengine6.bank import" not in code


@pytest.mark.unit
def test_gate_reuses_the_bank_session_and_token_checks():
    src = inspect.getsource(CasinoService._check_access)
    for name in ("_get_or_bind_session_for", "_validate_wire_token", "_validate_session_token"):
        assert name in src, name


@pytest.mark.unit
def test_main_constructs_and_registers_the_casino_service():
    from bed.main import BED

    src = inspect.getsource(BED)  # the daemon class builds and registers the services
    assert "CasinoService(" in src
    assert "no_casino_service" in src
    assert "self.casino_service.register_all(self.server)" in src


@pytest.mark.unit
def test_no_casino_service_flag_exists():
    from bed import lib

    p = argparse.ArgumentParser()
    lib.buildargs(p)
    assert p.parse_args([]).no_casino_service is False
    assert p.parse_args(["--no-casino-service"]).no_casino_service is True
