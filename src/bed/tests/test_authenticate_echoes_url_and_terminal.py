"""The default sign-in callable echoes the bed URL it will dial and the terminal report, at debug level.

The URL is ws://{host}:{port}{path} from the same endpoint defaults the login uses. The terminal report is
bbsengine6 io.terminal.report(). Both are gated on debug, so a normal sign-in prints nothing extra.
"""

import unittest
from unittest.mock import patch

from bed.tools import authenticate as auth


class TestEchoes(unittest.TestCase):
    def _run(self, debug):
        echoes = []
        with patch.object(auth, "_default_bed_args", return_value=auth._default_bed_args()), \
             patch.object(auth.presentation, "prompt_credentials", return_value=("jam", "pw")), \
             patch.object(auth, "_login", return_value=("ok", "tok", "jam")), \
             patch.object(auth.store, "store_token"), \
             patch.object(auth.io, "echo", side_effect=lambda t="", *a, **k: echoes.append((str(t), k.get("level")))), \
             patch.object(auth.io.terminal, "report", return_value={"term": "xterm-256color", "color": "256"}):
            auth.authenticate(directory="/tmp", store_token=False, debug=debug)
        return echoes

    def test_the_bed_url_is_echoed_at_debug_level(self):
        echoes = self._run(debug=True)
        self.assertIn(("bed url: ws://localhost:8765/", "debug"), echoes)

    def test_the_terminal_report_is_echoed_at_debug_level(self):
        echoes = self._run(debug=True)
        self.assertTrue(any("terminal" in t and lvl == "debug" for t, lvl in echoes), echoes)

    def test_nothing_is_echoed_without_debug(self):
        echoes = self._run(debug=False)
        self.assertFalse(any(lvl == "debug" for _, lvl in echoes), echoes)


if __name__ == "__main__":
    unittest.main()
