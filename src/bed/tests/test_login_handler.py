"""The seam login handler: a blank is refused before any check; a wrong password mints nothing; a good login returns a token.

The handler is the login core, shared by every front end. It does not bind a socket, print, or write a token file.
"""

import secrets
import unittest
from unittest.mock import patch

from bed.api.auth import AuthService
from bed.api.session import SessionRegistry
from bed.api.token_store import InMemoryTokenStore
from bed.api.login import handle_login
from bed.api.token_store import MemberInfo
from bbsengine6.seam.requests import LoginRequest
from bbsengine6.seam.codes import ErrorCode


class _Provider:
    def __init__(self, accept=True):
        self.accept = accept
        self.calls = []

    def authenticate(self, args, moniker, password, *, pool=None):
        self.calls.append((moniker, password))
        return MemberInfo(moniker=moniker, is_sysop=False, balance=42) if self.accept else None


def _service(provider):
    import argparse
    return AuthService(
        args=argparse.Namespace(debug=False, pool=None),
        session_registry=SessionRegistry(),
        token_store=InMemoryTokenStore(),
        credential_provider=provider,
        secret=secrets.token_bytes(32),
        instance_id="instance-A",
        ttl_seconds=900,
        clock=None,
    )


class TestBlankRefused(unittest.TestCase):
    def test_a_blank_moniker_is_refused_before_any_check(self):
        provider = _Provider()
        result = handle_login(_service(provider), LoginRequest(moniker="   ", password="pw"))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, ErrorCode.INVALID_CREDENTIALS.value)
        self.assertEqual(provider.calls, [], "no credential check runs for a blank moniker")

    def test_a_blank_password_is_refused_before_any_check(self):
        provider = _Provider()
        result = handle_login(_service(provider), LoginRequest(moniker="alice", password=""))
        self.assertFalse(result.ok)
        self.assertEqual(provider.calls, [])


class TestBadCredentials(unittest.TestCase):
    def test_a_wrong_password_mints_nothing_and_records_the_failure(self):
        service = _service(_Provider(accept=False))
        with patch.object(service, "_record_login_failed") as recorded:
            result = handle_login(service, LoginRequest(moniker="alice", password="wrong"))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, ErrorCode.INVALID_CREDENTIALS.value)
        self.assertNotIn("token", result.data)
        self.assertEqual(service.token_store._records, {}, "a refused login mints no token")
        recorded.assert_called_once()

    def test_the_refusal_names_no_field(self):
        result = handle_login(_service(_Provider(accept=False)), LoginRequest(moniker="alice", password="wrong"))
        self.assertNotIn("moniker", result.message.lower().replace("moniker or password", ""))


class TestGoodLogin(unittest.TestCase):
    def test_a_good_login_returns_the_token(self):
        result = handle_login(_service(_Provider()), LoginRequest(moniker="alice", password="pw"))
        self.assertTrue(result.ok)
        self.assertEqual(result.code, ErrorCode.OK.value)
        self.assertTrue(result.data["token"])
        self.assertEqual(result.data["moniker"], "alice")

    def test_the_handler_writes_no_token_file(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                handle_login(_service(_Provider()), LoginRequest(moniker="alice", password="pw"))
                self.assertEqual(os.listdir(d), [])
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
