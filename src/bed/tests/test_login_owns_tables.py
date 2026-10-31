"""Login-time owns_tables reads through engine.casino_read.

Bed does not read casino tables itself. ``read_owns_tables`` asks the definer
function ``engine.casino_read`` on bed's login role (``set_role=False``) and checks
the ``owner`` of each table returned. A failed read returns False and never raises.
"""

import unittest
from unittest.mock import MagicMock, patch


def _conn_returning(result):
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (result,)
    conn.cursor.return_value.__enter__.return_value = cur
    conn.__enter__.return_value = conn
    return conn, cur


class TestReadOwnsTables(unittest.TestCase):
    def _read(self, result, moniker="jam"):
        from bed.api import login_owns_tables

        conn, cur = _conn_returning(result)
        with patch("bbsengine6.database.connect", return_value=conn) as connect:
            value = login_owns_tables.read_owns_tables(
                args=MagicMock(), pool=MagicMock(), token_hash=b"h", moniker=moniker
            )
        return value, connect, cur

    def test_true_when_member_owns_a_table(self):
        result = {"success": True, "tables": [
            {"moniker": "t1", "owner": "jam"},
            {"moniker": "t2", "owner": "other"},
        ]}
        value, _connect, _cur = self._read(result)
        self.assertIs(value, True)

    def test_false_when_member_owns_no_table(self):
        result = {"success": True, "tables": [{"moniker": "t2", "owner": "other"}]}
        value, _connect, _cur = self._read(result)
        self.assertIs(value, False)

    def test_false_when_there_are_no_tables(self):
        value, _connect, _cur = self._read({"success": True, "tables": []})
        self.assertIs(value, False)

    def test_false_when_casino_read_reports_failure(self):
        value, _connect, _cur = self._read({"success": False, "error": "denied"})
        self.assertIs(value, False)

    def test_false_when_no_row_is_returned(self):
        from bed.api import login_owns_tables

        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn.cursor.return_value.__enter__.return_value = cur
        conn.__enter__.return_value = conn
        with patch("bbsengine6.database.connect", return_value=conn):
            value = login_owns_tables.read_owns_tables(
                args=MagicMock(), pool=MagicMock(), token_hash=b"h", moniker="jam"
            )
        self.assertIs(value, False)

    def test_accepts_jsonb_returned_as_a_string(self):
        import json

        result = {"success": True, "tables": [{"moniker": "t1", "owner": "jam"}]}
        value, _connect, _cur = self._read(json.dumps(result))
        self.assertIs(value, True)

    def test_uses_login_role_and_casino_read(self):
        value, connect, cur = self._read({"success": True, "tables": []})
        self.assertIs(connect.call_args.kwargs.get("set_role"), False)
        executed = [c.args[0] for c in cur.execute.call_args_list]
        self.assertTrue(any("engine.casino_read" in s for s in executed), executed)

    def test_a_database_error_returns_false_and_does_not_raise(self):
        from bed.api import login_owns_tables

        with patch("bbsengine6.database.connect", side_effect=RuntimeError("db down")):
            value = login_owns_tables.read_owns_tables(
                args=MagicMock(), pool=MagicMock(), token_hash=b"h", moniker="jam"
            )
        self.assertIs(value, False)


class TestEnvelopesCarryOwnsTables(unittest.TestCase):
    def _service(self):
        from bed.api.auth import AuthService

        service = AuthService.__new__(AuthService)
        service.args = MagicMock()
        return service

    def test_auth_result_envelope_includes_owns_tables(self):
        from bed.api.token_store import MemberInfo

        record = MagicMock(token="t", session_id="s", expires_at=0)
        info = MemberInfo(moniker="jam", is_sysop=False, balance=5, owns_tables=True)
        env = self._service()._auth_result_envelope(record, info, fresh=True)
        self.assertIs(env["owns_tables"], True)

    def test_reconnect_result_envelope_includes_owns_tables(self):
        from bed.api.token_store import MemberInfo

        record = MagicMock(token="t", session_id="s", expires_at=0)
        info = MemberInfo(moniker="jam", is_sysop=True, balance=5, owns_tables=False)
        env = self._service()._reconnect_result_envelope(record, info, None)
        self.assertIs(env["owns_tables"], False)

    def test_member_info_defaults_owns_tables_to_false(self):
        from bed.api.token_store import MemberInfo

        self.assertIs(MemberInfo(moniker="jam").owns_tables, False)


if __name__ == "__main__":
    unittest.main()
