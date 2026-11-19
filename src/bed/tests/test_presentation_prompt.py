"""The shared credentials prompt: the box, blank re-prompt, cancel, no echo of the password, and the plain fallback."""

import unittest
from unittest.mock import patch

from bed.tools import presentation


def _fake_frame(title, width, children=(), **_):
    from bbsengine6.util import Frame, ChildPlace
    frame = Frame(title=title, width=width, top=0, bottom=2)
    for i, child in enumerate(children):
        frame.children.append(ChildPlace(title=child.title, top=3, bottom=5, column=2))
    return frame


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
             patch.object(presentation, "draw_frame", side_effect=_fake_frame), patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: f), patch.object(presentation, "_plain"):
            moniker, password = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual((moniker, password), ("jam", "secret"))

    def test_a_blank_moniker_is_asked_again(self):
        with patch.object(presentation, "_read_field", _Answers("", "  ", "jam")) as field, \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "draw_frame", side_effect=_fake_frame), patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: f), patch.object(presentation, "_plain"):
            moniker, _ = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(moniker, "jam")
        self.assertEqual(len(field.asked), 3, "a blank moniker is asked again")

    def test_a_blank_password_is_asked_again(self):
        with patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("", "secret")) as pw, \
             patch.object(presentation, "draw_frame", side_effect=_fake_frame), patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: f), patch.object(presentation, "_plain"):
            _, password = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(password, "secret")
        self.assertEqual(len(pw.asked), 2, "a blank password is asked again")

    def test_ctrl_d_cancels_the_login(self):
        with patch.object(presentation, "_read_field", _Answers(EOFError())), \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "draw_frame", side_effect=_fake_frame), patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: f), patch.object(presentation, "_plain"):
            with self.assertRaises(presentation.CredentialsCancelled):
                presentation.prompt_credentials(title="zoid6 auth")

    def test_the_box_is_titled_with_the_project_title(self):
        drawn = []
        with patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "draw_frame", side_effect=lambda t, w, **k: (drawn.append(t), _fake_frame(t, w, **k))[1]), \
             patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: f), \
             patch.object(presentation, "_plain"):
            presentation.prompt_credentials(title="round-view login")
        self.assertIn("round-view login", drawn)

    def test_a_non_ansi_terminal_prints_plain_text_not_a_box(self):
        with patch.object(presentation, "_has_ansi", return_value=False), \
             patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("secret")), \
             patch.object(presentation, "draw_frame", side_effect=_fake_frame), patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: f) as box, patch.object(presentation, "_plain") as plain:
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
             patch.object(presentation, "draw_frame", side_effect=_fake_frame), patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: f), patch.object(presentation, "_plain"):
            _, password = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(password, "secret")
        self.assertEqual(len(masked), 1, "the password goes through the masked reader")
        self.assertIn("password", masked[0])

if __name__ == "__main__":
    unittest.main()


class TestAnsiDetection(unittest.TestCase):
    def test_the_box_check_asks_the_terminal_and_waits_for_the_answer(self):
        # has_ansi(block=False) returns False without probing, so every terminal got the plain line. The check must
        # wait for the terminal's answer, as the rest of bbsengine6 does.
        with patch.object(presentation.io.terminal, "has_ansi", return_value=True) as probe:
            self.assertTrue(presentation._has_ansi())
        probe.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()


class TestAnsiDetection(unittest.TestCase):
    def test_the_box_check_asks_the_terminal_and_waits_for_the_answer(self):
        # has_ansi(block=False) returns False without probing, so every terminal got the plain line. The check must
        # wait for the terminal's answer, as the rest of bbsengine6 does.
        with patch.object(presentation.io.terminal, "has_ansi", return_value=True) as probe:
            self.assertTrue(presentation._has_ansi())
        probe.assert_called_once_with()
