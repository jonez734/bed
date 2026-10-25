"""The per-request PostgreSQL role comes only from a bound session.

The role is process-wide (a context variable). A request with no bound session (auth,
reconnect) must get no role, so it never runs under a role a previous request left
behind. Otherwise login's SET LOCAL ROLE fails with permission denied.
"""

import unittest
from types import SimpleNamespace


class TestRoleForSession(unittest.TestCase):
    def test_bound_session_uses_member_moniker(self):
        from bed.main import role_for_session

        self.assertEqual(role_for_session(SimpleNamespace(moniker="jam")), "jam")

    def test_unbound_request_gets_no_role(self):
        from bed.main import role_for_session

        self.assertIsNone(role_for_session(None))


if __name__ == "__main__":
    unittest.main()
