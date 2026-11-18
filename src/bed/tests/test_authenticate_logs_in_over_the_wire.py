"""authenticate checks the password with the running bed, over the wire, never with a database of its own.

A front end (the TUI, the round viewer, a script) has no database configuration; the password check belongs to the daemon.
The shared sign-in must log in through BedAuthServiceClient, the same call bed auth login makes.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bed.tools import authenticate as auth_mod


class TestLoginOverTheWire(unittest.TestCase):
    def test_login_calls_the_running_bed_and_not_a_local_credential_store(self):
        reply = {"ok": True, "moniker": "alice", "token": "tok-1"}
        svc = MagicMock()
        svc.login = AsyncMock(return_value=reply)
        with patch.object(auth_mod, "_wire_login_client", return_value=svc) as client:
            status, token, moniker = auth_mod._login("alice", "pw")
        client.assert_called_once()
        svc.login.assert_awaited_once_with("alice", "pw")
        self.assertEqual((status, token, moniker), ("ok", "tok-1", "alice"))

    def test_a_refused_login_over_the_wire_returns_no_token(self):
        svc = MagicMock()
        svc.login = AsyncMock(return_value={"ok": False, "code": "bad_credentials"})
        with patch.object(auth_mod, "_wire_login_client", return_value=svc):
            status, token, _ = auth_mod._login("alice", "wrong")
        self.assertEqual(token, "")
        self.assertNotEqual(status, "ok")


if __name__ == "__main__":
    unittest.main()
