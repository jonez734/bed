"""The lastlogin write keeps bed's login role (set_role=False).

engine.bed_touch_lastlogin is a definer function bed calls as its own role. The
connection must not inherit the per-request member role set by _pre_dispatch.
"""

import unittest
from unittest.mock import MagicMock, patch


class TestTouchLastloginSetRole(unittest.TestCase):
    def test_touch_lastlogin_connects_with_set_role_false(self):
        from bed.api.auth import AuthService

        svc = AuthService.__new__(AuthService)
        svc.args = MagicMock(pool=MagicMock())
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchone.return_value = (None,)
        conn.cursor.return_value.__enter__.return_value = cur
        with patch("bbsengine6.database.connect") as connect, \
                patch("bbsengine6.database.cursor") as cursor:
            connect.return_value.__enter__.return_value = conn
            cursor.return_value.__enter__.return_value = cur
            svc._touch_lastlogin("tok", "jam", "127.0.0.1")
        connect.assert_called_once()
        self.assertIs(connect.call_args.kwargs.get("set_role"), False)


if __name__ == "__main__":
    unittest.main()
