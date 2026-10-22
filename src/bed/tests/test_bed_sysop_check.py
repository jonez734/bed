"""bed's SECURITY DEFINER functions check sysop through a zoid6-owned wrapper.

engine.checkmemberflag takes (flag_name, membermoniker) and is not security
definer, so bed (which has no member_flag privilege) cannot run it. Calling it
with the moniker first is also a bug: the moniker would be read as the flag name.
Each bed function must use bed_is_sysop(moniker), a zoid6-owned definer wrapper
that passes the arguments in the right order.
"""

import re
from pathlib import Path

import bed

SQL_DIR = Path(bed.__file__).parent / "sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def test_no_bed_function_calls_checkmemberflag_directly():
    # The wrapper is the one place that may name checkmemberflag.
    for f in sorted(SQL_DIR.glob("bed_*.sql")):
        if f.name == "bed_sysop_check.sql":
            continue
        assert "checkmemberflag" not in _code(f.read_text()), f.name


def test_wrapper_defined_as_definer_with_fixed_path_and_correct_argument_order():
    text = _code((SQL_DIR / "bed_sysop_check.sql").read_text())
    assert re.search(r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.bed_is_sysop\(p_moniker\s+citext\)", text, re.IGNORECASE)
    assert "SECURITY DEFINER" in text
    assert re.search(r"SET\s+search_path\s*=\s*engine\s*,\s*public\s*,\s*pg_temp", text, re.IGNORECASE)
    assert re.search(r"engine\.checkmemberflag\(\s*'sysop'\s*,\s*p_moniker::text\s*\)", text, re.IGNORECASE)


def test_wrapper_owned_by_zoid6_and_execute_only_to_bed():
    text = _code((SQL_DIR / "bed_sysop_check.sql").read_text())
    assert re.search(r"OWNER\s+TO\s+zoid6", text, re.IGNORECASE)
    assert re.search(r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+engine\.bed_is_sysop\(citext\)\s+FROM\s+PUBLIC", text, re.IGNORECASE)
    assert re.search(r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+engine\.bed_is_sysop\(citext\)\s+TO\s+bed", text, re.IGNORECASE)


def test_send_and_group_use_wrapper():
    for name in ("bed_message_send.sql", "bed_group_functions.sql"):
        assert "bed_is_sysop(" in _code((SQL_DIR / name).read_text()), name


def test_install_runs_wrapper_before_send():
    import importlib
    import inspect

    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup._ensure_bed_token_table)
    assert src.index('"bed_sysop_check.sql"') < src.index('"bed_message_send.sql"')


def test_wrapper_runs_checkmemberflag_as_zoid6_owner():
    # checkmemberflag is not SECURITY DEFINER. Its reads of member_flag, member
    # and map_member_flag run with the rights of whoever calls it. The wrapper
    # is definer, so those reads run as its owner, zoid6. That only works if
    # zoid6 owns every relation checkmemberflag reads.
    text = _code((SQL_DIR / "bed_sysop_check.sql").read_text())
    assert "SECURITY DEFINER" in text
    assert re.search(r"OWNER\s+TO\s+zoid6", text, re.IGNORECASE)


def test_owner_migration_covers_every_relation_checkmemberflag_reads():
    import importlib
    from pathlib import Path as _P
    bbs = _P(importlib.import_module("bbsengine6").__file__).parent
    migration = _code((bbs / "sql" / "owner_zoid6_migration.sql").read_text())
    for rel in ("member_flag", "map_member_flag"):
        assert re.search(rf"alter\s+table\s+engine\.{rel}\s+owner\s+to\s+zoid6", migration, re.IGNORECASE), rel
    # engine.member is a view; it must move with the same owner migration.
    assert re.search(r"alter\s+view\s+engine\.member\s+owner\s+to\s+zoid6", migration, re.IGNORECASE), "engine.member"
