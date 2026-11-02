"""bed's install manifest (5.1), install status report (5.2), and NEEDINFO guard (5.3).

The manifest checks need no database. The guard runs before any SQL, so it is
tested with no connection. The status report is tested with the catalog check
replaced by a fake.
"""

import json
from importlib import resources
from types import SimpleNamespace

import pytest

from bbsengine6.backend import objects
from bed import startup


def _manifest_text():
    return resources.files("bed").joinpath("sql/objects.json").read_text(encoding="utf-8")


# ---- 5.1 manifest ------------------------------------------------------------


def test_bed_manifest_validates():
    m = objects.load_manifest(json.loads(_manifest_text()))
    assert m.project == "bed"
    assert len(m.objects) == 18


def test_bed_manifest_names_only_bed_objects():
    """bed's manifest holds bed's own objects: no zoid6, casino, or other project names."""
    m = objects.load_manifest(json.loads(_manifest_text()))
    for obj in m.objects:
        assert "casino" not in obj.name, obj.label()
        assert "zoid6" not in obj.name, obj.label()
        assert obj.name.startswith("engine.bed_") or obj.name == "engine.__bed_token", obj.label()


def test_bed_manifest_has_the_token_table_and_17_functions():
    m = objects.load_manifest(json.loads(_manifest_text()))
    kinds = [o.kind for o in m.objects]
    assert kinds.count("table") == 1
    assert kinds.count("function") == 17


# ---- 5.3 guard ---------------------------------------------------------------


@pytest.mark.parametrize("value", ["NEEDINFO", "", None])
def test_install_refuses_unset_databasename_before_any_sql(value, monkeypatch):
    called = []
    monkeypatch.setattr(startup.startuplib, "runmodule", lambda *a, **k: called.append(a))
    args = SimpleNamespace(install=True, databasename=value)
    assert startup.ensure_startup(args) is False
    assert called == [], "no bootstrap SQL may run when the database name is unset"


def test_install_guard_message_says_what_to_pass(monkeypatch, capsys):
    monkeypatch.setattr(startup.startuplib, "runmodule", lambda *a, **k: pytest.fail("ran"))
    startup.ensure_startup(SimpleNamespace(install=True, databasename="NEEDINFO"))
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "--databasename" in out


def test_sentinel_is_bbsengine6_default():
    from bbsengine6.database import BBSENGINE6_DBNAME_DEFAULT

    assert startup.BED_DATABASENAME_UNSET == BBSENGINE6_DBNAME_DEFAULT == "NEEDINFO"


# ---- 5.2 status report -------------------------------------------------------


class _FakeReport:
    def __init__(self, status, present, missing):
        self.status = status
        self.present = present
        self.missing = missing


def _run_status(monkeypatch, report):
    monkeypatch.setattr(startup.database, "getpool", lambda *a, **k: object())

    class _Ctx:
        def __enter__(self):
            return object()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(startup.database, "connect", lambda *a, **k: _Ctx())
    monkeypatch.setattr(objects, "check_objects", lambda *a, **k: report)
    return startup._report_bed_status(SimpleNamespace(databasename="zoid6"))


def test_status_installed_returns_true(monkeypatch, capsys):
    ok = _run_status(monkeypatch, _FakeReport(objects.STATUS_INSTALLED, [1] * 18, []))
    assert ok is True
    assert "bed: installed" in capsys.readouterr().out


def test_status_partial_lists_missing_and_returns_false(monkeypatch, capsys):
    missing = [objects.ObjectRef("function", "engine.bed_token_get", "bytea")]
    ok = _run_status(monkeypatch, _FakeReport(objects.STATUS_PARTIAL, [1] * 17, missing))
    assert ok is False
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "partial" in out
    assert "engine.bed_token_get" in out


def test_status_not_installed_returns_false(monkeypatch, capsys):
    ok = _run_status(monkeypatch, _FakeReport(objects.STATUS_NOT_INSTALLED, [], []))
    assert ok is False
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "not installed" in out
