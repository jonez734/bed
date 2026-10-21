"""DBTokenStore stores and looks up tokens by SHA-256 hash only.

The raw bearer token must never reach SQL. The store module must not depend
on the auth service or the daemon, so other callers can reuse it.
"""

import ast
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import bed.api.token_store as ts


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class _FakeCursor:
    def __init__(self, rows=None, rowcount=1):
        self.rows = rows or []
        self.rowcount = rowcount
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None


def _store_with_cursor(cur):
    store = ts.DBTokenStore(MagicMock())
    conn = MagicMock()
    store._pool = lambda: MagicMock()
    return store, conn


def _patched(cur):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cur
    cursor_cm.__exit__.return_value = False
    conn.cursor = MagicMock(return_value=cursor_cm)
    return conn


def test_put_sends_hash_not_raw_token():
    cur = _FakeCursor()
    store = ts.DBTokenStore(MagicMock())
    store._pool = lambda: MagicMock()
    rec = ts.TokenRecord(
        token="RAW-SECRET-TOKEN", moniker="jam", session_id="s", issued_at=1.0,
        expires_at=2.0, is_sysop=False, bed_instance_id="i", websocket_id="w",
        claims={}, loginid=None,
    )
    with patch("bbsengine6.database.connect") as connect, \
         patch("bbsengine6.database.cursor") as cursor:
        connect.return_value.__enter__.return_value = MagicMock()
        cursor.return_value.__enter__.return_value = cur
        store.put(rec)
    params = cur.executed[0][1]
    assert "RAW-SECRET-TOKEN" not in [p for p in params if isinstance(p, str)]
    assert _digest("RAW-SECRET-TOKEN") in params


def test_get_queries_by_hash():
    cur = _FakeCursor(rows=[])
    store = ts.DBTokenStore(MagicMock())
    store._pool = lambda: MagicMock()
    with patch("bbsengine6.database.connect") as connect, \
         patch("bbsengine6.database.cursor") as cursor:
        connect.return_value.__enter__.return_value = MagicMock()
        cursor.return_value.__enter__.return_value = cur
        store.get("RAW-SECRET-TOKEN")
    params = cur.executed[0][1]
    assert _digest("RAW-SECRET-TOKEN") in params
    assert "RAW-SECRET-TOKEN" not in params


def test_delete_queries_by_hash():
    cur = _FakeCursor(rowcount=1)
    store = ts.DBTokenStore(MagicMock())
    store._pool = lambda: MagicMock()
    with patch("bbsengine6.database.connect") as connect, \
         patch("bbsengine6.database.cursor") as cursor:
        connect.return_value.__enter__.return_value = MagicMock()
        cursor.return_value.__enter__.return_value = cur
        assert store.delete("RAW-SECRET-TOKEN") is True
    assert _digest("RAW-SECRET-TOKEN") in cur.executed[0][1]


def test_store_module_has_no_auth_or_daemon_imports():
    src = Path(ts.__file__).read_text()
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(("bed.api.auth", "bed.main", "bed.api.session")):
                bad.append(node.module)
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith(("bed.api.auth", "bed.main")):
                    bad.append(a.name)
    assert bad == []


def test_sql_references_no_raw_token_column():
    src = Path(ts.__file__).read_text()
    assert "WHERE token = %s" not in src
    assert "token_hash" in src


def test_get_returns_caller_token_since_db_holds_only_hash():
    cur = _FakeCursor(rows=[{
        "moniker": "jam", "session_id": "s", "issued_at": 1.0, "expires_at": 9.0,
        "is_sysop": False, "bed_instance_id": "i", "websocket_id": "w",
        "claims": "{}", "loginid": None,
    }])
    store = ts.DBTokenStore(MagicMock())
    store._pool = lambda: MagicMock()
    with patch("bbsengine6.database.connect") as connect, \
         patch("bbsengine6.database.cursor") as cursor:
        connect.return_value.__enter__.return_value = MagicMock()
        cursor.return_value.__enter__.return_value = cur
        rec = store.get("RAW-SECRET-TOKEN")
    assert rec is not None
    assert rec.token == "RAW-SECRET-TOKEN"
