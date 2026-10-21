"""The token-store docs match the implementation.

They must describe the install-time schema (bed/sql/bed_token.sql), the SHA-256
hash, persistence across restarts, and the 300-second cleanup. They must not
describe the retired lazy path or the old data/sql location.
"""

import re
from pathlib import Path

import bed

ROOT = Path(bed.__file__).resolve().parents[2]
BED_AUTH = ROOT / "handbook" / "BED_AUTH.md"
README = ROOT / "README.md"
ARCH = ROOT / "handbook" / "ARCHITECTURE.md"


def _text(p: Path) -> str:
    return p.read_text()


def test_bed_auth_handbook_names_install_time_schema():
    t = _text(BED_AUTH)
    assert "bed/sql/bed_token.sql" in t
    assert "bed --install" in t


def test_bed_auth_handbook_no_longer_describes_lazy_schema():
    t = _text(BED_AUTH)
    assert "applied lazily" not in t
    assert "bed/data/sql/bed_token.sql" not in t


def test_bed_auth_handbook_states_hash_and_persistence():
    t = _text(BED_AUTH)
    assert "SHA-256" in t
    assert "survive" in t.lower() or "persist" in t.lower()


def test_bed_auth_handbook_states_300_second_cleanup():
    t = _text(BED_AUTH)
    assert "300" in t
    assert re.search(r"5 minutes|300 seconds", t)


def test_bed_auth_handbook_default_persistence_is_memory_in_code_db_in_zoid6():
    t = _text(BED_AUTH)
    assert "zoid6" in t and "token_persistence" in t and "db" in t


def test_readme_and_architecture_point_at_new_path():
    for p in (README, ARCH):
        t = _text(p)
        assert "data/sql/bed_token.sql" not in t, p.name
        assert "sql/bed_token.sql" in t, p.name


def test_token_persistence_help_names_install_path():
    import argparse
    from bed.lib import buildargs

    parser = argparse.ArgumentParser()
    buildargs(parser)
    action = next(a for a in parser._actions if a.dest == "token_persistence")
    assert "bed/data/sql" not in action.help
    assert "bed --install" in action.help
    assert "bed/sql/bed_token.sql" in action.help
