"""bed.json's bank.starting_balance is applied to bbsengine6 at startup, like session.idle_minutes.

The packaged default is 100. A missing key keeps the bbsengine6 default. A bad value stops startup
before anything runs.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import importlib

bedmain = importlib.import_module("bed.main")

PACKAGED = Path(bedmain.__file__).parent / "data" / "bed.json"


def test_packaged_bed_json_has_starting_balance_100():
    cfg = json.loads(PACKAGED.read_text())
    assert cfg["bank"]["starting_balance"] == 100
    assert "_comment" in cfg["bank"]


def test_apply_sets_the_starting_balance():
    from bbsengine6 import bank
    try:
        bedmain._apply_bank_config({"bank": {"starting_balance": 250}})
        assert bank.get_starting_balance() == 250
    finally:
        bank.configure(starting_balance=bank.DEFAULT_STARTING_BALANCE)


def test_missing_bank_section_keeps_the_default():
    from bbsengine6 import bank
    with patch.object(bank, "configure") as cfg:
        bedmain._apply_bank_config({})
    cfg.assert_not_called()


@pytest.mark.parametrize("bad", [-1, "100", True, 1.5])
def test_bad_values_stop_startup(bad):
    with pytest.raises(ValueError):
        bedmain._apply_bank_config({"bank": {"starting_balance": bad}})
