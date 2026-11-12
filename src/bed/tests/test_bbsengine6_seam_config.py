"""bed.json or zoid6.json's ``bbsengine6`` section sets the seam's settings at startup.

A missing section keeps the seam defaults. The section is passed to bbsengine6.seam.configure, so a
bad value stops startup before the daemon runs.
"""

import importlib
from unittest.mock import patch

import pytest

bed_main = importlib.import_module("bed.main")


@pytest.mark.unit
def test_missing_bbsengine6_section_changes_nothing():
    with patch("bbsengine6.seam.config.configure") as cfg:
        bed_main._apply_bbsengine6_config({})
    cfg.assert_not_called()


@pytest.mark.unit
def test_bbsengine6_section_is_passed_to_the_seam():
    with patch("bbsengine6.seam.config.configure") as cfg:
        bed_main._apply_bbsengine6_config(
            {"bbsengine6": {"seam": {"capacity": 500, "idle_seconds": 120, "logger_name": "zoid6.seam"}}}
        )
    cfg.assert_called_once_with(capacity=500, idle_seconds=120, logger_name="zoid6.seam")


@pytest.mark.unit
def test_bad_seam_value_raises_before_startup():
    with pytest.raises(ValueError):
        bed_main._apply_bbsengine6_config({"bbsengine6": {"seam": {"capacity": 0}}})


@pytest.mark.unit
def test_comment_keys_are_not_settings():
    with patch("bbsengine6.seam.config.configure") as cfg:
        bed_main._apply_bbsengine6_config(
            {"bbsengine6": {"seam": {"_comment": "note", "capacity": 500}}}
        )
    cfg.assert_called_once_with(capacity=500)


@pytest.mark.unit
def test_io_section_is_applied_to_bbsengine6_io():
    from bbsengine6.io import settings as io_settings

    io_settings.reset()
    try:
        bed_main._apply_io_config({"io": {"inputstring_pagesize": 4}})
        assert io_settings.get("inputstring_pagesize") == 4
    finally:
        io_settings.reset()
