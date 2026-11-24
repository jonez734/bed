"""The sign-in is described by a Form: a moniker textbox, then a password textbox masked with "" (nothing shown).

authenticate() builds the form and hands it to the presentation. prompt_credentials declares form last (the contract's
position) and reads the two textboxes from it. A call with the old keywords builds the same form.
"""

import inspect
import unittest
from unittest.mock import patch

from bbsengine6.form import Form, FormItemTextbox

from bed.tools import authenticate as auth, presentation


class TestSigninForm(unittest.TestCase):
    def test_the_sign_in_form_is_a_moniker_then_a_password(self):
        form = auth.signin_form(title="zoid6 auth")
        self.assertIsInstance(form, Form)
        self.assertEqual([item.name for item in form], ["moniker", "password"])
        self.assertEqual([item.label for item in form], [presentation.LABEL_MONIKER, presentation.LABEL_PASSWORD])

    def test_the_password_textbox_shows_nothing_by_default(self):
        password = auth.signin_form(title="zoid6 auth")[1]
        self.assertIsInstance(password, FormItemTextbox)
        self.assertEqual(password.mask, "")


class TestPresentationContract(unittest.TestCase):
    def test_prompt_credentials_declares_form_last_after_the_original_keywords(self):
        names = list(inspect.signature(presentation.prompt_credentials).parameters)
        self.assertEqual(names[:3], ["title", "header", "width"])
        self.assertIn("form", names)
        self.assertEqual(names.index("form"), 3)

    def test_the_shipped_presentation_passes_the_contract(self):
        from bbsengine6 import module as be6
        self.assertIsNone(be6.check_signature(presentation.prompt_credentials, be6._stub_prompt_credentials,
                                              name="prompt_credentials"))


class TestReadsTheForm(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(presentation, "_has_ansi", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_moniker_and_password_come_from_the_form_textboxes(self):
        asked = []
        form = Form("zoid6 auth", [FormItemTextbox(label=presentation.LABEL_MONIKER, name="moniker"),
                                   FormItemTextbox(label=presentation.LABEL_PASSWORD, name="password", mask="")])
        with patch.object(presentation, "_read_field", side_effect=lambda p, w: asked.append(p) or "jam"), \
             patch.object(presentation, "_read_password", side_effect=lambda p, w: asked.append(p) or "pw"):
            result = presentation.prompt_credentials(title="zoid6 auth", form=form)
        self.assertEqual(result, ("jam", "pw"))
        self.assertIn(presentation.LABEL_MONIKER, asked[0])
        self.assertIn(presentation.LABEL_PASSWORD, asked[1])


if __name__ == "__main__":
    unittest.main()
