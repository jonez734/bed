"""The sign-in is one frame holding both fields (bbsengine6 demo_authenticate): no box grown below, no absolute move.

prompt_credentials draws one frame with two interior rows. The moniker is read on row 0; after_field() records the line
break; the password is read on row 1. Nothing uses extend_frame or {curpos}.
"""

import unittest
from unittest.mock import patch

from bed.tools import presentation


class _Answers:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, prompt, *args, **kwargs):
        self.asked.append(prompt)
        return self.answers.pop(0)


class TestOneFrame(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(presentation, "_has_ansi", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_frame_is_drawn_with_two_interior_rows(self):
        with patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("pw")), \
             patch.object(presentation, "draw_frame", wraps=presentation.draw_frame) as draw:
            presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(draw.call_count, 1)
        self.assertEqual(draw.call_args.kwargs.get("height"), 2)

    def test_the_moniker_is_read_then_the_password_on_the_second_row(self):
        frame_calls = []

        class _Frame:
            def paint_label(self, index, text):
                frame_calls.append(("label", index, text))

            def place_row(self, index):
                frame_calls.append(("place_row", index))

            def after_field(self):
                frame_calls.append(("after_field",))

        with patch.object(presentation, "draw_frame", return_value=_Frame()), \
             patch.object(presentation, "_read_field", side_effect=lambda p, w: frame_calls.append(("moniker",)) or "jam"), \
             patch.object(presentation, "_read_password", side_effect=lambda p, w: frame_calls.append(("password",)) or "pw"):
            presentation.prompt_credentials(title="zoid6 auth")
        self.assertEqual(frame_calls, [
            ("label", 0, "moniker:"), ("label", 1, "password:"), ("place_row", 0), ("moniker",),
            ("after_field",), ("place_row", 1), ("password",),
        ])

    def test_extend_frame_is_not_used(self):
        with patch.object(presentation, "_read_field", _Answers("jam")), \
             patch.object(presentation, "_read_password", _Answers("pw")), \
             patch("bbsengine6.util.extend_frame") as extend:
            presentation.prompt_credentials(title="zoid6 auth")
        extend.assert_not_called()
        self.assertFalse(hasattr(presentation, "_extend"), "the grow-below helper is removed")


if __name__ == "__main__":
    unittest.main()
