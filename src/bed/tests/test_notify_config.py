#!/usr/bin/env python3
# bed/tests/test_notify_config.py
# notify.bell_window_seconds from bed.json reaches bbsengine6.io, and bad values fail fast.

import json
import os
from unittest.mock import patch

import pytest

import importlib

# ``bed.main`` the package attribute is the entry function, not the module.
bed_main = importlib.import_module("bed.main")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BED_JSON = os.path.join(_HERE, "..", "data", "bed.json")


def test_packaged_bed_json_has_notify_default():
    with open(_BED_JSON, encoding="utf-8") as fh:
        cfg = json.load(fh)
    assert cfg["notify"]["bell_window_seconds"] == 5


def test_apply_sets_bell_window():
    with patch("bbsengine6.io.set_bell_window_seconds") as setter:
        bed_main._apply_notify_config({"notify": {"bell_window_seconds": 8}})
    setter.assert_called_once_with(8.0)


def test_missing_section_leaves_default():
    with patch("bbsengine6.io.set_bell_window_seconds") as setter:
        bed_main._apply_notify_config({})
    setter.assert_not_called()


@pytest.mark.parametrize("bad", [-1, "5", True, [5]])
def test_bad_value_rejected(bad):
    with pytest.raises(ValueError):
        bed_main._apply_notify_config({"notify": {"bell_window_seconds": bad}})
