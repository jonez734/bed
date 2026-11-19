"""The auth frame grows: the moniker box is drawn first, and on Enter the password box is added below it, at the same width.

The password is read only after the moniker is accepted and the password box is drawn. The plain-text fallback draws no
frame and emits no save or restore sequences.
"""

import unittest
from unittest.mock import patch

from bbsengine6 import util

from bed.tools import presentation


class _Answers:
    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, prompt, *args, **kwargs):
        return self.answers.pop(0)


class TestAuthFrameGrows(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(presentation, "_has_ansi", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_password_box_is_drawn_after_the_moniker_is_accepted(self):
        order = []
        with patch.object(presentation, "_read_field", side_effect=lambda p, w: order.append("moniker") or "jam"), \
             patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: order.append("password box") or f), \
             patch.object(presentation, "_read_password", side_effect=lambda p, w: order.append("password") or "pw"):
            presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(order, ["moniker", "password box", "password"])

    def test_the_password_box_is_the_same_width_as_the_moniker_box(self):
        calls = []
        with patch.object(presentation, "_read_field", side_effect=_Answers("jam")), \
             patch.object(presentation, "_read_password", side_effect=_Answers("pw")), \
             patch.object(presentation, "_extend", side_effect=lambda f, *a, **k: calls.append(k) or f):
            presentation.prompt_credentials(title="zoid6 auth", width=25)
        self.assertEqual(calls[0]["width"], 25)

    def test_the_password_is_read_masked_inside_the_grown_frame(self):
        with patch.object(presentation, "_read_field", side_effect=_Answers("jam")), \
             patch.object(presentation, "_extend"), \
             patch.object(presentation, "_read_password", side_effect=_Answers("pw")) as pw:
            _, password = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(password, "pw")
        self.assertEqual(pw.call_count, 1)


class TestPlainFallback(unittest.TestCase):
    def test_the_plain_fallback_draws_no_frame_and_no_save_restore(self):
        with patch.object(presentation, "_has_ansi", return_value=False), \
             patch.object(presentation, "_read_field", side_effect=_Answers("jam")), \
             patch.object(presentation, "_read_password", side_effect=_Answers("pw")), \
             patch.object(presentation, "_extend") as extend, \
             patch.object(util.io, "echo") as echo:
            presentation.prompt_credentials(title="zoid6 auth")
        extend.assert_not_called()
        texts = [str(c.args[0]) for c in echo.call_args_list if c.args]
        self.assertFalse(any("decsc" in t or "decrc" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
