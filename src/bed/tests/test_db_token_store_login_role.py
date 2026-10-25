"""DBTokenStore calls engine.bed_token_* definer functions as bed's login role.

A plain connect inherits the process-wide member role, so put() right after a login
fails with permission denied to set role. Both put and get must keep bed's login role.
"""

import unittest
from unittest.mock import MagicMock, patch


class TestDBTokenStoreLoginRole(unittest.TestCase):
    def _store(self):
        from bed.api.token_store import DBTokenStore

        return DBTokenStore(MagicMock(pool=MagicMock()))

    def test_put_connects_with_set_role_false(self):
        from bed.api.token_store import TokenRecord
        from bbsengine6 import database

        store = self._store()
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cur
        conn.__enter__.return_value = conn
        record = MagicMock(spec=TokenRecord)
        with patch.object(database, "connect", return_value=conn) as connect, \
                patch.object(database, "cursor", return_value=cur), \
                patch("bed.store.token_store.put_params", return_value=()):
            store.put(record)

        self.assertIs(connect.call_args.kwargs.get("set_role"), False)

    def test_get_connects_with_set_role_false(self):
        from bbsengine6 import database

        store = self._store()
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = None
        conn.cursor.return_value.__enter__.return_value = cur
        conn.__enter__.return_value = conn
        with patch.object(database, "connect", return_value=conn) as connect, \
                patch.object(database, "cursor", return_value=cur):
            store.get("tok")

        self.assertIs(connect.call_args.kwargs.get("set_role"), False)


if __name__ == "__main__":
    unittest.main()
