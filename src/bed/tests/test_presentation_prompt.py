"""The shared credentials prompt: the box, blank re-prompt, cancel, no echo of the password, and the plain fallback."""

import unittest
from unittest.mock import patch

from bed.tools import presentation


class _Answers:
    """Feeds prompt answers in order and records what was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, prompt, *args, **kwargs):
        self.asked.append(prompt)
        value = self.answers.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class TestPromptCredentials(unittest.TestCase):
    def setUp(self):
        # The terminal's ANSI support is probed on stdin; the tests fix it so they never depend on the real terminal.
        patcher = patch.object(presentation, "_has_ansi", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_the_moniker_and_password_entered(self):
        with patch.object(presentation, "_read_field", _Answers("jam", "secret")), \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "_box"), patch.object(presentation, "_plain"):
            moniker, password = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual((moniker, password), ("jam", "secret"))

    def test_a_blank_moniker_is_asked_again(self):
        with patch.object(presentation, "_read_field", _Answers("", "  ", "jam")) as field, \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "_box"), patch.object(presentation, "_plain"):
            moniker, _ = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(moniker, "jam")
        self.assertEqual(len(field.asked), 3, "a blank moniker is asked again")

    def test_a_blank_password_is_asked_again(self):
        with patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("", "secret")) as pw, \
             patch.object(presentation, "_box"), patch.object(presentation, "_plain"):
            _, password = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(password, "secret")
        self.assertEqual(len(pw.asked), 2, "a blank password is asked again")

    def test_ctrl_d_cancels_the_login(self):
        with patch.object(presentation, "_read_field", _Answers(EOFError())), \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "_box"), patch.object(presentation, "_plain"):
            with self.assertRaises(presentation.CredentialsCancelled):
                presentation.prompt_credentials(title="zoid6 auth")

    def test_the_box_is_titled_with_the_project_title(self):
        drawn = []
        with patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "_box", side_effect=lambda title, width: drawn.append(title)), \
             patch.object(presentation, "_plain"):
            presentation.prompt_credentials(title="round-view login")
        self.assertIn("round-view login", drawn)

    def test_a_non_ansi_terminal_prints_plain_text_not_a_box(self):
        with patch.object(presentation, "_has_ansi", return_value=False), \
             patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "_box") as box, patch.object(presentation, "_plain") as plain:
            presentation.prompt_credentials(title="zoid6 auth")
        box.assert_not_called()
        plain.assert_called()

    def test_the_password_is_read_masked_and_not_echoed(self):
        masked = []

        def fake_password(prompt, *args, **kwargs):
            masked.append(prompt)
            return "secret"

        with patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", side_effect=fake_password), \
             patch.object(presentation, "_box"), patch.object(presentation, "_plain"):
            _, password = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(password, "secret")
        self.assertEqual(len(masked), 1, "the password goes through the masked reader")
        self.assertIn("password", masked[0])

if __name__ == "__main__":
    unittest.main()
