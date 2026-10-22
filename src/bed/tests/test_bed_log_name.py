"""bed's warnings and errors carry the configured bed name as the logger name.

The journal prefix is the logger name, so every bed warning or error shows the same
name (for example zoid6[pid]:), whichever module logged it.
"""

import importlib
import logging
from types import SimpleNamespace

import pytest

main = importlib.import_module("bed.main")


@pytest.mark.unit
def test_bed_logger_is_named_from_config():
    logger = main.bed_logger(SimpleNamespace(bed_name="zoid6"))
    assert logger.name == "zoid6"


@pytest.mark.unit
def test_two_daemons_with_different_names_log_different_prefixes():
    a = main.bed_logger(SimpleNamespace(bed_name="zoid6"))
    b = main.bed_logger(SimpleNamespace(bed_name="other"))
    assert a.name != b.name


@pytest.mark.unit
def test_module_loggers_are_routed_under_the_bed_tree_with_the_bed_name_handler():
    bed_logger = main.bind_bed_log_name("zoid6")
    child = logging.getLogger("bed.api.bank")
    chain = []
    node = child
    while node is not None:
        chain.append(node.name)
        node = node.parent
    assert "bed" in chain
    named = [h for h in bed_logger.handlers if getattr(h, "_bed_name", None) == "zoid6"]
    assert named, "the bed tree must have a handler that prints the configured name"
    assert "zoid6[" in named[0].formatter._fmt


@pytest.mark.unit
def test_warning_from_a_bed_module_is_formatted_with_the_bed_name():
    bed_logger = main.bind_bed_log_name("zoid6")
    named = [h for h in bed_logger.handlers if getattr(h, "_bed_name", None) == "zoid6"]
    record = logging.LogRecord("bed.api.bank", logging.WARNING, __file__, 1, "probe failed", None, None)
    assert named[0].format(record).startswith("zoid6[")
    assert "probe failed" in named[0].format(record)
