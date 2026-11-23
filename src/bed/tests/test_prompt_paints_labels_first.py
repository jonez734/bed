"""The auth frame paints every child's label before the first read, then moves back to the first child.

The moniker and password labels are written on their rows as the frame is shown. The cursor then returns to row 0 (the
moniker) before the moniker is read, so the first read starts on the first child.
"""

import unittest
from unittest.mock import patch

from bed.tools import presentation


class TestLabelsLineUp(unittest.TestCase):
    """The moniker and password labels are padded to one width, so the values after them start in the same column."""

    def test_the_labels_are_the_same_width(self):
        self.assertEqual(len(presentation.LABEL_MONIKER), len(presentation.LABEL_PASSWORD))

    def test_the_values_start_in_the_same_column(self):
        # The text typed after each label starts at the label's width, so both values begin in one column.
        self.assertEqual(len(presentation.LABEL_MONIKER), len(presentation.LABEL_PASSWORD))

    def test_the_read_prompts_use_the_same_labels_as_the_painted_ones(self):
        seen = []
        with patch.object(presentation, "_has_ansi", return_value=True), \
             patch.object(presentation, "draw_frame", return_value=type("F", (), {
                 "paint_label": lambda self, i, t: None, "place_row": lambda self, i: None,
                 "after_field": lambda self: None})()), \
             patch.object(presentation, "_read_field", side_effect=lambda p, w: seen.append(p) or "jam"), \
             patch.object(presentation, "_read_password", side_effect=lambda p, w: seen.append(p) or "pw"):
            presentation.prompt_credentials(title="zoid6 auth")
        self.assertIn(presentation.LABEL_MONIKER, seen[0])
        self.assertIn(presentation.LABEL_PASSWORD, seen[1])


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
        self.assertEqual([e[2] for e in painted], [presentation.LABEL_MONIKER, presentation.LABEL_PASSWORD])

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
