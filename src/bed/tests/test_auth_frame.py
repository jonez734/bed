"""The sign-in is one frame: the moniker is read on the first row, the password on the second, in the same box.

The password is read only after the moniker is accepted. The plain-text fallback draws no frame and emits no save or
restore sequences.
"""

import unittest
from unittest.mock import patch

from bed.tools import presentation


class _Answers:
    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, prompt, *args, **kwargs):
        return self.answers.pop(0)


class TestAuthFrameIsOne(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(presentation, "_has_ansi", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_password_is_read_after_the_moniker_in_the_same_frame(self):
        order = []
        with patch.object(presentation, "_read_field", side_effect=lambda p, w: order.append("moniker") or "jam"), \
             patch.object(presentation, "_read_password", side_effect=lambda p, w: order.append("password") or "pw"):
            presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(order, ["moniker", "password"])

    def test_the_frame_has_two_rows_and_the_password_row_is_the_second(self):
        rows = []

        class _Frame:
            def paint_label(self, index, text):
                rows.append(("label", index, text))

            def place_row(self, index):
                rows.append(index)

            def after_field(self):
                rows.append("after")

        with patch.object(presentation, "draw_frame", return_value=_Frame()), \
             patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("pw")):
            presentation.prompt_credentials(title="zoid6 auth", width=25)
        self.assertEqual(rows, [("label", 0, "moniker:"), ("label", 1, "password:"), 0, "after", 1])

    def test_the_password_is_read_masked_inside_the_frame(self):
        prompts = []

        def fake_password(prompt, *args, **kwargs):
            prompts.append(prompt)
            return "pw"

        with patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", side_effect=fake_password):
            _, password = presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(password, "pw")
        self.assertEqual(len(prompts), 1)
        self.assertIn("password", prompts[0])


class TestPlainFallback(unittest.TestCase):
    def test_the_plain_fallback_draws_no_frame_and_no_save_restore(self):
        with patch.object(presentation, "_has_ansi", return_value=False), \
             patch.object(presentation, "_read_field", side_effect=_Answers("jam")), \
             patch.object(presentation, "_read_password", side_effect=_Answers("pw")), \
             patch.object(presentation, "draw_frame") as draw, \
             patch.object(presentation.io, "echo") as echo:
            presentation.prompt_credentials(title="zoid6 auth")
        draw.assert_not_called()
        texts = [str(c.args[0]) for c in echo.call_args_list if c.args]
        self.assertFalse(any("decsc" in t or "decrc" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
