"""Install-time vs runtime startup modes.

The service start path must not run the bbsengine6 bootstrap (which
includes the superuser gate and DDL). Only ``bed --install``, run once
per install by the owner, runs the full bootstrap.
"""

import argparse
from unittest.mock import MagicMock, patch

import bed.startup as bedstartup
from bed.lib import buildargs


def _args(install: bool):
    parser = argparse.ArgumentParser()
    buildargs(parser)
    args = parser.parse_args([])
    args.install = install
    args.databasename = "zoid6"
    return args


def test_buildargs_defines_install_flag():
    parser = argparse.ArgumentParser()
    buildargs(parser)
    assert parser.parse_args([]).install is False
    assert parser.parse_args(["--install"]).install is True


def test_runtime_does_not_run_bootstrap():
    args = _args(install=False)
    with patch.object(bedstartup.startuplib, "runmodule") as bootstrap, \
         patch.object(bedstartup, "verify_install", return_value=True) as verify:
        assert bedstartup.ensure_startup(args) is True
    bootstrap.assert_not_called()
    verify.assert_called_once()


def test_install_runs_full_bootstrap():
    args = _args(install=True)
    with patch.object(bedstartup.startuplib, "runmodule", return_value=True) as bootstrap, \
         patch.object(bedstartup.database, "getpool") as getpool, \
         patch.object(bedstartup.database, "connect") as connect, \
         patch.object(bedstartup, "_ensure_bed_role", return_value=True), \
         patch.object(bedstartup, "verify_install") as verify:
        connect.return_value.__enter__.return_value = MagicMock()
        assert bedstartup.ensure_startup(args) is True
    bootstrap.assert_called_once()
    verify.assert_not_called()


def test_verify_install_true_when_engine_schema_present():
    args = _args(install=False)
    with patch.object(bedstartup.database, "getpool") as getpool, \
         patch.object(bedstartup.database, "connect") as connect, \
         patch.object(bedstartup.database, "schemaexists", return_value=True) as exists:
        connect.return_value.__enter__.return_value = MagicMock()
        assert bedstartup.verify_install(args) is True
    exists.assert_called_once()
    assert exists.call_args.args[1] == "engine"


def test_verify_install_false_when_engine_schema_missing():
    args = _args(install=False)
    with patch.object(bedstartup.database, "getpool"), \
         patch.object(bedstartup.database, "connect") as connect, \
         patch.object(bedstartup.database, "schemaexists", return_value=False):
        connect.return_value.__enter__.return_value = MagicMock()
        assert bedstartup.verify_install(args) is False
