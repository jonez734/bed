"""Bed's per-player token file: the name every front end uses, and the owner-only store."""

import hashlib
import os
import re
import stat
import tempfile
import unittest
from unittest.mock import patch

from bed.tools import token_name, store

SHAPE = re.compile(r"^round-view\.[a-z0-9_-]+\.[A-Za-z0-9_-]+\.[0-9a-f]{8}\.token$")


class TestTokenName(unittest.TestCase):
    def test_with_a_tty_the_name_carries_the_tty_and_the_hash(self):
        with patch.object(token_name, "_tty_name", return_value="pts_4"):
            name = token_name.token_file_name("jam")
        self.assertEqual(name, f"round-view.jam.pts_4.{hashlib.sha256(b'jam|pts_4').hexdigest()[:8]}.token")
        self.assertRegex(name, SHAPE)

    def test_with_no_tty_the_name_hashes_the_pid(self):
        with patch.object(token_name, "_tty_name", return_value=""):
            name = token_name.token_file_name("jam", pid=7)
        self.assertEqual(name, f"round-view.jam.notty.{hashlib.sha256(b'jam|pid7').hexdigest()[:8]}.token")

    def test_case_folds_so_one_player_has_one_file(self):
        with patch.object(token_name, "_tty_name", return_value="pts_4"):
            self.assertEqual(token_name.token_file_name("Jam"), token_name.token_file_name("jam"))

    def test_an_unsafe_moniker_is_refused(self):
        for bad in ("../etc", "jam/x", "jam.x", "", "j a m"):
            with self.assertRaises(ValueError):
                token_name.token_file_name(bad)


class TestStoreToken(unittest.TestCase):
    def test_the_token_is_written_owner_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "round-view.jam.pts_4.abcdef12.token")
            store.store_token(path, "tok-secret")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), "tok-secret")

    def test_a_second_store_replaces_the_token(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.token")
            store.store_token(path, "one")
            store.store_token(path, "two")
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read().strip(), "two")


if __name__ == "__main__":
    unittest.main()
