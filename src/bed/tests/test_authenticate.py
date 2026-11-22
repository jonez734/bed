"""authenticate: prompt, log in through the seam, store the token. The old names stay as deprecated aliases."""

import os
import tempfile
import unittest
import warnings
from unittest.mock import patch

from bed.tools import authenticate as auth_mod


class TestAuthenticate(unittest.TestCase):
    def test_a_good_login_stores_the_token_and_returns_it(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(auth_mod, "_presentation", return_value=lambda **k: ("jam", "secret")), \
             patch.object(auth_mod, "_login", return_value=("ok", "tok-1", "jam")):
            moniker, token = auth_mod.authenticate(directory=d, title="zoid6 auth", fd=0)
            self.assertEqual((moniker, token), ("jam", "tok-1"))
            self.assertEqual(len(os.listdir(d)), 1)

    def test_a_refused_login_stores_nothing_and_returns_no_token(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(auth_mod, "_presentation", return_value=lambda **k: ("jam", "wrong")), \
             patch.object(auth_mod, "_login", return_value=("invalid_credentials", "", "jam")):
            moniker, token = auth_mod.authenticate(directory=d, title="zoid6 auth", fd=0)
            self.assertEqual(token, "")
            self.assertEqual(os.listdir(d), [])


class TestDeprecatedAliases(unittest.TestCase):
    def test_the_old_login_name_still_works_and_warns(self):
        with patch.object(auth_mod, "authenticate", return_value=("jam", "tok")) as new:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                result = auth_mod.auth_login_alias(directory="/x", title="t", fd=0)
        self.assertEqual(result, ("jam", "tok"))
        new.assert_called_once()
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))


if __name__ == "__main__":
    unittest.main()
