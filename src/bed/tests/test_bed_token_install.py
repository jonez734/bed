"""bed --install creates the token table; the service start path does not.

The SQL ships as bed package data under bed/sql/, and the install path loads
it as the owner via bbsengine6's importsql.
"""

import importlib
import inspect
import re
from pathlib import Path

import bed


PYPROJECT = Path(bed.__file__).parent.parent.parent / "pyproject.toml"


def test_pyproject_declares_bed_sql_package_data():
    text = PYPROJECT.read_text()
    m = re.search(r'^bed\s*=\s*\[(.*?)\]', text, re.MULTILINE | re.DOTALL)
    assert m, "package-data for bed not found"
    entries = m.group(1)
    assert '"sql/*.sql"' in entries, "bed package-data must include sql/*.sql"
    assert "data/sql" not in entries, "stale data/sql entry must be removed"


def test_install_loads_bed_token_schema():
    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup._ensure_bed_token_table)
    assert 'importsql(' in src
    assert '"bed_token.sql"' in src
    assert 'package="bed"' in src


def test_install_branch_calls_token_schema_after_role_setup():
    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup.ensure_startup)
    install_branch = src[src.index("startuplib.runmodule(args, \"main\")"):]
    assert install_branch.index("_ensure_bed_role") < install_branch.index("_ensure_bed_token_table")


def test_runtime_path_does_not_create_token_table():
    startup = importlib.import_module("bed.startup")
    src = inspect.getsource(startup.verify_install)
    assert "_ensure_bed_token_table" not in src
    assert "importsql" not in src
