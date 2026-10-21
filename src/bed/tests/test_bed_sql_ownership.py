"""Bed-only SQL lives in bed, and bed --install runs it.

bbsengine6 must not contain bed's SQL. bed --install runs the bed-owned files
after the bed role and token table, as the owner.
"""

import importlib
import inspect
from pathlib import Path

import bed

BED_SQL = Path(bed.__file__).parent / "sql"
BBS_SQL = Path(importlib.import_module("bbsengine6").__file__).parent / "sql"


def test_message_read_functions_live_in_bed():
    assert (BED_SQL / "bed_message_read_functions.sql").exists()


def test_service_grants_live_in_bed():
    assert (BED_SQL / "bed_service_grants.sql").exists()


def test_bbsengine6_has_no_bed_sql():
    leftovers = [p.name for p in BBS_SQL.glob("bed_*.sql")]
    assert leftovers == [], f"bed SQL must move out of bbsengine6: {leftovers}"


def test_install_runs_service_grants_and_read_functions():
    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup._ensure_bed_token_table)
    # Token files are installed in _ensure_bed_token_table; the read API and
    # grants follow in the same install pass.
    assert "bed_message_read_functions.sql" in src
    assert "bed_service_grants.sql" in src


def test_install_order_grants_after_read_functions():
    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup._ensure_bed_token_table)
    assert src.index("bed_message_read_functions.sql") < src.index("bed_service_grants.sql")
