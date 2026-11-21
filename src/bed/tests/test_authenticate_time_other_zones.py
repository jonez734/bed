"""time_line() shows the local time in each zone with its zone name, and UTC in parentheses.

Each case sets TZ, calls tzset(), checks the output, and restores TZ. The UTC instant is fixed, so the expected local
clock is exact. Zones with a half-hour or 45-minute offset and a numeric abbreviation are included.
"""

import os
import time
import unittest
from unittest.mock import patch

import datetime as dt

from bed.tools import authenticate as auth

UTC_INSTANT = dt.datetime(2026, 11, 21, 13, 49, 7, tzinfo=dt.timezone.utc)


class _FixedNow(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return UTC_INSTANT if tz is None else UTC_INSTANT.astimezone(tz)


CASES = [
    ("America/Los_Angeles", "2026-11-21 05:49:07 PST (2026-11-21 13:49:07 UTC)"),
    ("America/New_York", "2026-11-21 08:49:07 EST (2026-11-21 13:49:07 UTC)"),
    ("Europe/London", "2026-11-21 13:49:07 GMT (2026-11-21 13:49:07 UTC)"),
    ("Asia/Kolkata", "2026-11-21 19:19:07 IST (2026-11-21 13:49:07 UTC)"),
    ("Pacific/Chatham", "2026-11-22 03:34:07 +1345 (2026-11-21 13:49:07 UTC)"),
]


class TestTimeLineZones(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("TZ")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._saved
        time.tzset()

    def test_each_zone_shows_its_local_clock_and_zone_name(self):
        for zone, expected in CASES:
            with self.subTest(zone=zone):
                os.environ["TZ"] = zone
                time.tzset()
                with patch.object(auth, "datetime", _FixedNow):
                    self.assertEqual(auth.time_line(), expected)


if __name__ == "__main__":
    unittest.main()
