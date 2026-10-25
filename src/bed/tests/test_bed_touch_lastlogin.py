"""Password logins record the previous lastlogin; token reconnects do not.

bed writes lastlogin only through the zoid6-owned SECURITY DEFINER function
engine.bed_touch_lastlogin, which returns the value before this login.
"""

import importlib
import inspect
import re
from pathlib import Path

import pytest

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_lastlogin_functions.sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


@pytest.mark.unit
def test_touch_function_defined_with_hash_and_moniker():
    t = _code(SQL.read_text())
    m = re.search(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.bed_touch_lastlogin\s*\((.*?)\)\s*RETURNS",
                  t, re.IGNORECASE | re.DOTALL)
    assert m, "bed_touch_lastlogin not defined"
    assert "p_token_hash bytea" in m.group(1) or "p_moniker" in m.group(1)


@pytest.mark.unit
def test_touch_function_returns_previous_value_and_sets_now():
    t = _code(SQL.read_text())
    assert re.search(r"lastlogin", t, re.IGNORECASE)
    assert re.search(r"SET\s+lastlogin\s*=\s*now\(\)", t, re.IGNORECASE)


@pytest.mark.unit
def test_touch_function_is_definer_owned_by_zoid6_and_execute_only_to_bed():
    t = _code(SQL.read_text())
    assert re.search(r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+engine\.bed_touch_lastlogin\(.*?\)\s+FROM\s+PUBLIC", t, re.IGNORECASE | re.DOTALL)
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_touch_lastlogin\(.*?\)\s+TO\s+bed", t, re.IGNORECASE | re.DOTALL)
    assert re.search(r"OWNER\s+TO\s+zoid6", t, re.IGNORECASE)


@pytest.mark.unit
def test_password_login_result_includes_last_login():
    from bed.api import auth as auth_mod
    src = inspect.getsource(auth_mod)
    assert "last_login" in src
    assert "bed_touch_lastlogin" in src


@pytest.mark.unit
def test_reconnect_does_not_touch_lastlogin():
    from bed.api import auth as auth_mod
    cls = next(v for v in vars(auth_mod).values() if inspect.isclass(v) and hasattr(v, "_reconnect_result_envelope"))
    src = inspect.getsource(cls._reconnect_result_envelope)
    assert "last_login" not in src
    assert "bed_touch_lastlogin" not in src


# --- Phase 3: client address and the login row ----------------------------------------------

@pytest.mark.unit
def test_touch_function_takes_the_client_address():
    t = _code(SQL.read_text())
    m = re.search(r"FUNCTION\s+engine\.bed_touch_lastlogin\s*\((.*?)\)\s*RETURNS",
                  t, re.IGNORECASE | re.DOTALL)
    assert m and "p_remoteaddr inet" in m.group(1), "bed_touch_lastlogin must take p_remoteaddr inet"


@pytest.mark.unit
def test_touch_function_sets_lastloginfrom_and_writes_the_login_row():
    t = _code(SQL.read_text())
    assert re.search(r"lastloginfrom\s*=\s*p_remoteaddr", t, re.IGNORECASE)
    assert re.search(r"engine\.log_action\s*\(", t, re.IGNORECASE), "login must be written through log_action"
    assert re.search(r"'login'", t)


@pytest.mark.unit
def test_touch_function_grant_is_signature_matched_to_the_new_arguments():
    t = _code(SQL.read_text())
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_touch_lastlogin\(bytea,\s*citext,\s*inet\)\s+TO\s+bed",
                     t, re.IGNORECASE)


@pytest.mark.unit
def test_caller_passes_the_websocket_peer_address():
    from bed.api import auth as auth_mod
    helper = inspect.getsource(auth_mod._peer_address)
    assert "remote_address" in helper, "the peer address must come from the websocket"
    handler = inspect.getsource(auth_mod.AuthService._handle_auth)
    assert "_peer_address(websocket)" in handler
    assert "_touch_lastlogin(" in handler


@pytest.mark.unit
def test_peer_address_is_read_from_the_websocket():
    from bed.api import auth as auth_mod
    class _WS:
        remote_address = ("192.0.2.7", 51234)
    assert auth_mod._peer_address(_WS()) == "192.0.2.7"
    class _NoPeer:
        pass
    assert auth_mod._peer_address(_NoPeer()) is None
