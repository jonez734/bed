"""bed records why a client ended, through engine.bed_client_exit.

The client sends client_exit with a reason; bed checks the reason against the fixed vocabulary
(the same one engine.log_action enforces) and calls the definer function with the token digest.
A rejected password also writes a login_failed row, through engine.bed_login_failed.
"""

import inspect
import re
from pathlib import Path

import bed
from bed.api import auth as auth_mod
from bed.api.auth import AuthService, CLIENT_EXIT_REASONS

SQL_DIR = Path(bed.__file__).parent / "sql"
WORKSPACE = Path(__file__).resolve().parents[4]  # .../data/work
LOG_ACTION = WORKSPACE / "bbsengine6" / "py" / "src" / "bbsengine6" / "sql" / "log_action.sql"


def test_client_exit_is_a_handled_message_type():
    assert "client_exit" in AuthService.HANDLED_TYPES
    assert auth_mod._OP_TO_HANDLER["client_exit"] is AuthService._handle_client_exit


def test_client_exit_message_type_reaches_its_handler_through_dispatch():
    # The type must be in _TYPE_TO_OP too, or handle_message drops it silently.
    assert auth_mod._TYPE_TO_OP["client_exit"] == "client_exit"
    import asyncio
    svc = AuthService.__new__(AuthService)
    svc.args = None
    out = asyncio.run(svc.handle_message(None, None, "/", {"type": "client_exit", "reason": "boredom", "token": "t", "moniker": "m"}))
    assert out is not None and out["type"] == "error"


def test_reason_vocabulary_matches_the_database_function():
    text = LOG_ACTION.read_text()
    m = re.search(r"p_note not in \(([^)]*)\)", text)
    assert m, "log_action.sql no longer validates the client_exit reason"
    db_reasons = set(re.findall(r"'([a-z_]+)'", m.group(1)))
    assert db_reasons == set(CLIENT_EXIT_REASONS)


def test_ws_error_is_a_client_exit_reason_in_both_places():
    assert "ws_error" in CLIENT_EXIT_REASONS
    assert "'ws_error'" in LOG_ACTION.read_text()


def test_client_exit_action_is_allowed_by_the_database_function():
    assert "'client_exit'" in LOG_ACTION.read_text()


def test_handler_refuses_an_unknown_reason_before_touching_the_database():
    svc = AuthService.__new__(AuthService)
    svc.args = None
    out = __import__("asyncio").run(
        svc._handle_client_exit(None, {"reason": "boredom", "token": "t", "moniker": "m"})
    )
    assert out["type"] == "error"
    assert out["code"] == "invalid_request"


def test_handler_requires_token_and_moniker():
    svc = AuthService.__new__(AuthService)
    svc.args = None
    out = __import__("asyncio").run(
        svc._handle_client_exit(None, {"reason": "eof", "token": "", "moniker": "m"})
    )
    assert out["type"] == "error"


def test_handler_calls_the_definer_function_with_the_token_digest(monkeypatch):
    calls = []

    class _Cur:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _Cur()

        def commit(self):
            pass

    from bbsengine6 import database
    monkeypatch.setattr(database, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(database, "cursor", lambda conn: _Cur())
    svc = AuthService.__new__(AuthService)
    svc.args = None
    out = __import__("asyncio").run(
        svc._handle_client_exit(None, {"reason": "eof", "token": "tok", "moniker": "jam", "remoteaddr": "10.0.0.5"})
    )
    assert out == {"type": "client_exit_result", "success": True}
    sql, params = calls[0]
    assert "engine.bed_client_exit(%s, %s, %s, %s)" in sql
    assert params[1:] == ("jam", "eof", "10.0.0.5")
    assert params[0] != "tok"  # the plaintext token never reaches the database


def test_failed_login_records_login_failed_through_its_own_function():
    text = (SQL_DIR / "bed_login_failed_functions.sql").read_text()
    assert "PERFORM engine.log_action(p_moniker, 'login_failed'" in text
    src = inspect.getsource(AuthService._handle_auth)
    assert "_record_login_failed(" in src


def test_definer_functions_are_zoid6_owned_and_bed_executable_only():
    for name in ("bed_client_exit_functions.sql", "bed_login_failed_functions.sql"):
        t = (SQL_DIR / name).read_text()
        assert "SECURITY DEFINER" in t
        assert "OWNER TO zoid6" in t
        assert "TO bed;" in t
        assert "FROM PUBLIC" in t


def test_both_definer_files_are_in_the_install_list():
    from bed import startup
    src = inspect.getsource(startup)
    assert "bed_client_exit_functions.sql" in src
    assert "bed_login_failed_functions.sql" in src
