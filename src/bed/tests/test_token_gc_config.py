"""bed.json's bed.token_gc_interval_seconds sets how often expired bearer tokens are removed.

A missing key keeps the packaged default (300 seconds). A bad value is rejected before startup, the
same way the session and tz settings are.
"""

import importlib

import pytest

bed_main = importlib.import_module("bed.main")


@pytest.mark.unit
def test_missing_key_keeps_the_default():
    assert bed_main._token_gc_interval({}) == 300


@pytest.mark.unit
def test_missing_bed_section_keeps_the_default():
    assert bed_main._token_gc_interval({"bed": {}}) == 300


@pytest.mark.unit
def test_configured_interval_is_used():
    assert bed_main._token_gc_interval({"bed": {"token_gc_interval_seconds": 60}}) == 60


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -5, "300", 2.5, True])
def test_bad_interval_is_rejected(bad):
    with pytest.raises(ValueError):
        bed_main._token_gc_interval({"bed": {"token_gc_interval_seconds": bad}})
