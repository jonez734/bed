"""authenticate asks the login presentation named by bed.json ``auth.presentation``.

The shipped default is the terminal presentation. A configured stand-in is what authenticate calls, and its cancel
propagates. A value that does not match the presentation contract fails when it is resolved, with the key named.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from bbsengine6 import module as be6_module
from bed.tools import authenticate as auth, presentation
from bed.tools.presentation import CredentialsCancelled

BED_JSON = Path(auth.__file__).resolve().parents[1] / "data" / "bed.json"


class TestShippedPresentation(unittest.TestCase):
    def test_bed_json_names_the_terminal_presentation(self):
        spec = json.loads(BED_JSON.read_text(encoding="utf-8"))["auth"]["presentation"]
        self.assertEqual(spec, "bed.tools.presentation:prompt_credentials")

    def test_the_shipped_value_matches_the_contract(self):
        spec = json.loads(BED_JSON.read_text(encoding="utf-8"))["auth"]["presentation"]
        fn = be6_module.resolve_callable(spec, be6_module._stub_prompt_credentials)
        self.assertIs(fn, presentation.prompt_credentials)


class TestPresentationAcceptsExtraOptions(unittest.TestCase):
    def test_the_terminal_presentation_takes_an_unknown_keyword(self):
        import inspect
        params = inspect.signature(presentation.prompt_credentials).parameters
        self.assertTrue(any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()))


class TestConfiguredPresentation(unittest.TestCase):
    def test_authenticate_calls_the_configured_presentation(self):
        calls = []

        def stand_in(*, title="login", header=None, width=25, **kwargs):
            calls.append((title, header, width))
            return ("jam", "pw")

        with patch.object(auth, "_presentation", return_value=stand_in), \
             patch.object(auth, "_login", return_value=("ok", "tok", "jam")), \
             patch.object(auth.store, "store_token"):
            auth.authenticate(directory="/tmp", title="zoid6 auth", header="v1", store_token=False)
        self.assertEqual(calls, [("zoid6 auth", "v1", 25)])

    def test_a_cancel_from_the_presentation_propagates(self):
        def cancelled(**kwargs):
            raise CredentialsCancelled()

        with patch.object(auth, "_presentation", return_value=cancelled):
            with self.assertRaises(CredentialsCancelled):
                auth.authenticate(directory="/tmp", store_token=False)


if __name__ == "__main__":
    unittest.main()
