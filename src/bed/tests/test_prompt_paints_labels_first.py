"""The auth frame paints every child's label before the first read, then moves back to the first child.

The moniker and password labels are written on their rows as the frame is shown. The cursor then returns to row 0 (the
moniker) before the moniker is read, so the first read starts on the first child.
"""

import unittest
from unittest.mock import patch

from bed.tools import presentation


class TestLabelsPaintedFirst(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(presentation, "_has_ansi", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_both_labels_are_painted_before_the_moniker_is_read(self):
        events = []

        class _Frame:
            def paint_label(self, index, text):
                events.append(("label", index, text))

            def place_row(self, index):
                events.append(("place_row", index))

            def after_field(self):
                events.append(("after_field",))

        with patch.object(presentation, "draw_frame", return_value=_Frame()), \
             patch.object(presentation, "_read_field", side_effect=lambda p, w: events.append(("moniker",)) or "jam"), \
             patch.object(presentation, "_read_password", side_effect=lambda p, w: events.append(("password",)) or "pw"):
            presentation.prompt_credentials(title="zoid6 auth")

        first_read = events.index(("moniker",))
        painted = [e for e in events[:first_read] if e[0] == "label"]
        self.assertEqual([e[2] for e in painted], ["moniker:", "password:"])

    def test_the_cursor_returns_to_the_first_child_before_the_moniker_read(self):
        events = []

        class _Frame:
            def paint_label(self, index, text):
                events.append(("label", index, text))

            def place_row(self, index):
                events.append(("place_row", index))

            def after_field(self):
                events.append(("after_field",))

        with patch.object(presentation, "draw_frame", return_value=_Frame()), \
             patch.object(presentation, "_read_field", side_effect=lambda p, w: events.append(("moniker",)) or "jam"), \
             patch.object(presentation, "_read_password", side_effect=lambda p, w: events.append(("password",)) or "pw"):
            presentation.prompt_credentials(title="zoid6 auth")

        first_read = events.index(("moniker",))
        self.assertEqual(events[first_read - 1], ("place_row", 0))


if __name__ == "__main__":
    unittest.main()
