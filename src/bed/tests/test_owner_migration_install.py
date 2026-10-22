"""bed --install runs the owner migration as the owner; the service start path never does.

The migration moves the bank, member, flag, and message objects to zoid6. It runs
only from the install branch, after the bbsengine6 bootstrap, and only when the
connecting role is a superuser or a member of zoid6.
"""

import importlib
import inspect
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import bbsengine6

startup = importlib.import_module("bed.startup")
MIGRATION = Path(bbsengine6.__file__).parent / "sql" / "owner_zoid6_migration.sql"


def _code(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))


@pytest.mark.unit
def test_install_branch_runs_owner_migration_before_token_table():
    src = inspect.getsource(startup.ensure_startup)
    assert "_ensure_owner_migration(" in src
    assert src.index("_ensure_owner_migration(") < src.index("_ensure_bed_token_table(")


@pytest.mark.unit
def test_owner_migration_loads_from_bbsengine6_package():
    src = inspect.getsource(startup._ensure_owner_migration)
    assert 'importsql(' in src
    assert '"owner_zoid6_migration.sql"' in src
    assert 'package="bbsengine6"' in src


@pytest.mark.unit
def test_owner_migration_refuses_a_non_owner_role():
    conn = MagicMock()
    with patch.object(startup, "_is_owner_or_superuser", return_value=False), \
         patch.object(startup.database, "importsql") as imp:
        assert startup._ensure_owner_migration(MagicMock(), conn) is False
    imp.assert_not_called()


@pytest.mark.unit
def test_owner_migration_runs_for_an_owner_role():
    conn = MagicMock()
    with patch.object(startup, "_is_owner_or_superuser", return_value=True), \
         patch.object(startup.database, "importsql", return_value=True) as imp:
        assert startup._ensure_owner_migration(MagicMock(), conn) is True
    imp.assert_called_once()
    assert imp.call_args.args[1] == "owner_zoid6_migration.sql"


@pytest.mark.unit
def test_runtime_path_never_runs_the_owner_migration():
    for fn in (startup.verify_install, startup.ensure_startup):
        src = inspect.getsource(fn)
        if fn is startup.ensure_startup:
            # only the install branch may call it
            pre, _, _ = src.partition("if not getattr(args, \"install\", False):")
            branch = src[len(pre):]
            assert "_ensure_owner_migration(" not in pre
        else:
            assert "_ensure_owner_migration(" not in src


@pytest.mark.unit
def test_migration_file_is_only_owner_moves_in_one_transaction():
    t = _code(MIGRATION.read_text())
    statements = [s.strip() for s in t.split(";") if s.strip()]
    body = [s for s in statements if s.lower() not in ("begin", "commit")]
    assert statements[0].lower() == "begin" and statements[-1].lower() == "commit"
    assert body and all(re.match(r"^alter\s+(table|view|sequence|schema|function)\s", s, re.IGNORECASE) for s in body)
    assert not re.search(r"owner\s+to\s+bed\b", t, re.IGNORECASE)


@pytest.mark.unit
def test_owner_check_reads_a_dict_row():
    """bbsengine6 cursors return dict rows; the check must read by column name."""
    cur = MagicMock()
    cur.fetchone.return_value = {"is_owner": True}
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    with patch.object(startup.database, "cursor") as dbc:
        dbc.return_value.__enter__.return_value = cur
        assert startup._is_owner_or_superuser(MagicMock(), conn) is True


@pytest.mark.unit
def test_owner_check_is_false_for_a_non_owner_dict_row():
    cur = MagicMock()
    cur.fetchone.return_value = {"is_owner": False}
    conn = MagicMock()
    with patch.object(startup.database, "cursor") as dbc:
        dbc.return_value.__enter__.return_value = cur
        assert startup._is_owner_or_superuser(MagicMock(), conn) is False
