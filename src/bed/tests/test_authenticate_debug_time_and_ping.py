"""The debug echoes at sign-in: the local time with UTC in parens, then the bed ping result.

The time is local with the zone name, and UTC in parentheses. The ping is the pong dict laid out one key per line with
the colour tokens; when the daemon is down it says so and the prompt still runs.
"""

import datetime as dt
import unittest
from unittest.mock import patch

from bed.tools import authenticate as auth
from bed.tools.ping import PingUnavailable


def _fixed_local(hour=14):
    """A local time with a fixed zone: 14:03:22 at UTC-05:00 (EST), i.e. 19:03:22 UTC."""
    tz = dt.timezone(dt.timedelta(hours=-5), "EST")
    return dt.datetime(2026, 11, 20, hour, 3, 22, tzinfo=tz)


class _Clock(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _fixed_local()


class TestTimeEcho(unittest.TestCase):
    def test_the_local_time_and_utc_are_echoed_in_the_documented_form(self):
        echoes = []
        with patch.object(auth, "datetime", _Clock), \
             patch.object(auth.io, "echo", side_effect=lambda t="", *a, **k: echoes.append(str(t))):
            text = auth.time_line()
        self.assertEqual(text, "2026-11-20 14:03:22 EST (2026-11-20 19:03:22 UTC)")


class TestPingEcho(unittest.TestCase):
    def test_a_pong_is_laid_out_one_key_per_line_with_the_colour_tokens(self):
        lines = auth.ping_lines({"type": "pong", "timestamp": 1.5})
        self.assertEqual(lines[0], "bed ping:")
        self.assertIn("{labelcolor}type: {valuecolor}pong", lines)
        self.assertIn("{labelcolor}timestamp: {valuecolor}1.5", lines)

    def test_an_unavailable_daemon_is_reported_and_the_prompt_still_runs(self):
        prompted = []
        echoes = []
        args = auth._default_bed_args()
        with patch.object(auth, "_default_bed_args", return_value=args), \
             patch.object(auth, "_bed_ping", side_effect=PingUnavailable("localhost", 8765, OSError("refused"))), \
             patch.object(auth.presentation, "prompt_credentials",
                          side_effect=lambda **k: prompted.append(k) or ("jam", "pw")), \
             patch.object(auth, "_login", return_value=("ok", "tok", "jam")), \
             patch.object(auth.store, "store_token"), \
             patch.object(auth.io, "echo", side_effect=lambda t="", *a, **k: echoes.append((str(t), k.get("level")))), \
             patch.object(auth, "time_line", return_value="t"):
            auth.authenticate(directory="/tmp", store_token=False, debug=True)
        self.assertTrue(prompted, "the prompt is still reached")
        self.assertTrue(any("bed ping: unavailable" in t and lvl == "debug" for t, lvl in echoes), echoes)


if __name__ == "__main__":
    unittest.main()
