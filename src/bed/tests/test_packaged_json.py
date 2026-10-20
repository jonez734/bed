#!/usr/bin/env python3
# bed/tests/test_packaged_json.py
# Guard against invalid or duplicated content in packaged JSON config files.

import json
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BED_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))

PACKAGED_JSON = [
    os.path.join(_BED_ROOT, "src", "bed", "data", "bed.json"),
    os.path.join(_BED_ROOT, "usr", "share", "factory", "etc", "bed", "bed.json"),
]


def _reject_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


class TestPackagedJson(unittest.TestCase):
    def test_packaged_json_parses_without_duplicate_keys(self):
        for path in PACKAGED_JSON:
            with self.subTest(path=path):
                with open(path, encoding="utf-8") as fh:
                    # object_pairs_hook raises on duplicate keys within an object.
                    # json.load itself raises on trailing content (e.g. a stray tail).
                    json.load(fh, object_pairs_hook=_reject_duplicate_keys)

    def test_bed_json_has_single_root_object(self):
        path = PACKAGED_JSON[0]
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh, object_pairs_hook=_reject_duplicate_keys)
        self.assertIsInstance(data, dict)
        self.assertIn("websocket", data)
        self.assertIn("postoffice", data)


if __name__ == "__main__":
    unittest.main()
