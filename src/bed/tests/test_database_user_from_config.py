"""A database user in the config applies when the CLI did not set one.

The loader used to treat "databaseuser equals the default" as "not set on the CLI", so a config user was
is ignored unless the CLI left the argparse default in place. zoid6 names its own database user in zoid6.json,
so the config value must apply when the CLI did not set one.
"""

import argparse
import importlib

import pytest

main_mod = importlib.import_module("bed.main")


def _args(user):
    return argparse.Namespace(databaseuser=user, databasepassword=None, databasename="zoid6",
                              databasehost="localhost", databaseport=5432)


@pytest.mark.unit
def test_config_user_applies_when_the_cli_left_the_default():
    args = _args(None)  # the real argparse default for databaseuser, i.e. not set on the CLI
    main_mod._apply_database_config(args, {"database": {"user": "zoid6"}})
    assert args.databaseuser == "zoid6"


@pytest.mark.unit
def test_an_explicit_cli_user_is_kept_over_the_config():
    args = _args("someone-else")  # set on the command line
    main_mod._apply_database_config(args, {"database": {"user": "zoid6"}})
    assert args.databaseuser == "someone-else"
