"""bed manages message groups through zoid6-owned functions that take the token hash.

The acting moniker comes from the engine.__bed_token row for that hash. bed holds
EXECUTE only. Rules:
- create: any live session may create a group; the creator becomes its first member.
- add / remove member: sysop, or a current member of that group.
- delete group: sysop, or the group's creator.
"""

import re
from pathlib import Path

import bed

SQL = Path(bed.__file__).parent / "sql" / "bed_group_functions.sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


def _text() -> str:
    return _code(SQL.read_text())


def test_file_exists():
    assert SQL.exists(), f"missing {SQL}"


def test_three_group_functions_defined_taking_hash():
    text = _text()
    for name in ("bed_group_create", "bed_group_add_member", "bed_group_remove_member", "bed_group_delete"):
        m = re.search(rf"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+engine\.{name}\s*\((.*?)\)\s*RETURNS", text, re.IGNORECASE | re.DOTALL)
        assert m, name
        assert "p_token_hash bytea" in m.group(1), name
        assert "moniker" not in m.group(1).lower() or "member" in m.group(1).lower(), name


def test_acting_moniker_from_session_row():
    text = _text()
    assert len(re.findall(r"FROM\s+engine\.__bed_token", text, re.IGNORECASE)) >= 4
    assert len(re.findall(r"expires_at\s*>\s*now\(\)", text, re.IGNORECASE)) >= 4


def test_add_and_remove_require_sysop_or_member():
    text = _text()
    assert len(re.findall(r"engine\.checkmemberflag\(\s*v_actor\s*,\s*'sysop'\s*\)", text, re.IGNORECASE)) >= 3


def test_delete_requires_sysop_or_creator():
    text = _text()
    assert re.search(r"createdby", text, re.IGNORECASE)


def test_security_definer_pinned_and_execute_only_to_bed():
    text = _text()
    assert len(re.findall(r"SECURITY\s+DEFINER", text, re.IGNORECASE)) == 4
    assert len(re.findall(r"SET\s+search_path\s*=\s*engine\s*,\s*pg_temp", text, re.IGNORECASE)) == 4
    assert len(re.findall(r"TO\s+bed\b", text, re.IGNORECASE)) == 4
    assert len(re.findall(r"FROM\s+PUBLIC", text, re.IGNORECASE)) == 4


def test_install_runs_group_functions_before_grants():
    import importlib
    import inspect

    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup._ensure_bed_token_table)
    assert '"bed_group_functions.sql"' in src
    assert src.index('"bed_message_send.sql"') < src.index('"bed_group_functions.sql"') < src.index('"bed_service_grants.sql"')
