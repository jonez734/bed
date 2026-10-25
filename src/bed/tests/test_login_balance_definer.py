"""Login balance reads go through bed's definer function, not bbsengine6's direct table.

bbsengine6's getcredits() runs bank.get_balance on a connection that inherits the
per-request member role, and fails with "permission denied to set role" or
"permission denied for table __account". bed's login balance read must use
engine.bed_bank_balance on a set_role=False connection, like the bank handlers.
"""

import unittest
from unittest.mock import MagicMock, patch


class TestLoginBalanceDefiner(unittest.TestCase):
    def test_read_login_balance_uses_definer_function_and_no_role_switch(self):
        from bed.api import login_balance

        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = (True, 42)
        conn.cursor.return_value.__enter__.return_value = cur
        conn.__enter__.return_value = conn

        with patch("bbsengine6.database.connect", return_value=conn) as connect, \
                patch("bbsengine6.member.getcredits") as getcredits:
            balance = login_balance.read_login_balance(
                args=MagicMock(), pool=MagicMock(), token_hash=b"h", moniker="jam"
            )

        self.assertEqual(balance, 42)
        getcredits.assert_not_called()
        self.assertIs(connect.call_args.kwargs.get("set_role"), False)
        executed = [c.args[0] for c in cur.execute.call_args_list]
        self.assertTrue(any("engine.bed_bank_balance" in s for s in executed), executed)

    def test_read_login_balance_returns_zero_when_no_account(self):
        from bed.api import login_balance

        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = (False, 0)
        conn.cursor.return_value.__enter__.return_value = cur
        conn.__enter__.return_value = conn

        with patch("bbsengine6.database.connect", return_value=conn):
            balance = login_balance.read_login_balance(
                args=MagicMock(), pool=MagicMock(), token_hash=b"h", moniker="nobody"
            )

        self.assertEqual(balance, 0)


class TestPasswordLoginDoesNotCallGetcredits(unittest.TestCase):
    def test_credential_provider_does_not_read_balance_via_getcredits(self):
        import inspect
        from bed.api import credential_provider

        src = inspect.getsource(credential_provider)
        self.assertNotIn(
            "member.getcredits(", src,
            "the password login must not read the balance through bbsengine6's "
            "direct-table getcredits; auth reads it through engine.bed_bank_balance "
            "after the token is minted",
        )


if __name__ == "__main__":
    unittest.main()


class TestReconnectBalanceDefiner(unittest.TestCase):
    def test_reconnect_does_not_call_getcredits(self):
        import inspect
        from bed.api import auth

        src = inspect.getsource(auth.AuthService._handle_reconnect)
        self.assertNotIn(
            "getcredits(", src,
            "reconnect must read the balance through engine.bed_bank_balance, not "
            "bbsengine6's direct-table getcredits",
        )

