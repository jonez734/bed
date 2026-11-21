"""The configured sign-in callable: bed.json names it, and it resolves against bbsengine6's contract stub.

auth.authenticate is "module:attr". The shipped default must resolve to a callable that matches _stub_authenticate, so a
front end that loads it at startup fails only if the contract is broken.
"""

import json
import unittest
from pathlib import Path

from bbsengine6 import module as be6_module


BED_JSON = Path(__file__).resolve().parents[1] / "data" / "bed.json"


class TestShippedAuthenticate(unittest.TestCase):
    def test_bed_json_names_an_authenticate_callable(self):
        auth = json.loads(BED_JSON.read_text(encoding="utf-8"))["auth"]
        self.assertEqual(auth.get("authenticate"), "bed.tools.authenticate:authenticate")

    def test_the_shipped_callable_matches_the_contract(self):
        spec = json.loads(BED_JSON.read_text(encoding="utf-8"))["auth"]["authenticate"]
        fn = be6_module.resolve_callable(spec, be6_module._stub_authenticate)
        self.assertTrue(callable(fn))


if __name__ == "__main__":
    unittest.main()


class TestAuthenticateAcceptsExtraOptions(unittest.TestCase):
    """The contract allows a callable to take extra keywords, so a future option does not break a configured callable."""

    def test_authenticate_accepts_an_unknown_keyword(self):
        import inspect
        from bed.tools.authenticate import authenticate

        self.assertTrue(
            any(p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(authenticate).parameters.values()),
            "authenticate must take **kwargs, as the stub does",
        )
