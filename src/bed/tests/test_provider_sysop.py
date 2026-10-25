"""The password provider reports is_sysop from member.is_sysop (flag OR sysop role).

A sysop by flag or by role must come back is_sysop=True through the provider.
"""

import unittest
from unittest.mock import MagicMock, patch


class TestProviderSysop(unittest.TestCase):
    def test_is_sysop_true_flows_into_member_info(self):
        from bed.api import credential_provider as cp

        with patch("bbsengine6.member.checkpassword", return_value=True), \
                patch("bbsengine6.member.moniker_exists", return_value=True), \
                patch("bbsengine6.member.is_sysop", return_value=True) as is_sysop, \
                patch("bbsengine6.member.getcredits", return_value=0), \
                patch.object(cp, "_lookup_loginid", return_value="jam_os"):
            info = cp.PasswordCredentialProvider().authenticate(
                MagicMock(), "jam", "pw", pool=MagicMock()
            )

        is_sysop.assert_called_once()
        self.assertTrue(info.is_sysop)


if __name__ == "__main__":
    unittest.main()
