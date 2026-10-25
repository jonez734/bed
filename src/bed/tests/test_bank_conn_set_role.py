"""Bank and message connections keep bed's login role (set_role=False).

Bed's definer-function calls must run as bed's own role. The daemon sets the
context role to the logged-in member for every request, so an inherited
SET LOCAL ROLE fails with "permission denied to set role".
"""

import unittest
from unittest.mock import MagicMock, patch


def _service_cls():
    from bed.api.bank import BankService

    return BankService


class TestBankConnSetRole(unittest.TestCase):
    def test_bank_conn_passes_set_role_false(self):
        from bed.api.bank import BankService

        svc = BankService.__new__(BankService)
        svc.args = MagicMock()
        svc._pool = MagicMock()
        with patch("bbsengine6.database.connect") as connect:
            svc._bank_conn()
        connect.assert_called_once()
        self.assertIs(connect.call_args.kwargs.get("set_role"), False)


class TestMessageConnSetRole(unittest.TestCase):
    def test_message_conn_passes_set_role_false(self):
        from bed.api.message import MessageService

        svc = MessageService.__new__(MessageService)
        svc.args = MagicMock()
        svc._pool = MagicMock()
        with patch("bbsengine6.database.connect") as connect:
            svc._message_conn()
        connect.assert_called_once()
        self.assertIs(connect.call_args.kwargs.get("set_role"), False)


if __name__ == "__main__":
    unittest.main()
