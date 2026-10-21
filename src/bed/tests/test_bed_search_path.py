"""bed's SECURITY DEFINER functions must see the citext type.

citext is installed in the public schema. A search_path of "engine, pg_temp"
hides it, so function bodies that declare citext fail at install time. Every
bed function pins search_path to engine, public, pg_temp.
"""

import re
from pathlib import Path

import bed

SQL_DIR = Path(bed.__file__).parent / "sql"


def test_every_bed_function_path_includes_public():
    for f in sorted(SQL_DIR.glob("bed_*.sql")):
        text = f.read_text()
        for m in re.finditer(r"SET\s+search_path\s*=\s*([^\n;]+)", text, re.IGNORECASE):
            path = [p.strip() for p in m.group(1).split(",")]
            assert path == ["engine", "public", "pg_temp"], f"{f.name}: {path}"


def test_no_bed_function_path_is_missing_public():
    for f in sorted(SQL_DIR.glob("bed_*.sql")):
        assert not re.search(r"SET\s+search_path\s*=\s*engine\s*,\s*pg_temp", f.read_text(), re.IGNORECASE), f.name
