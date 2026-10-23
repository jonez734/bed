"""Per-account token files: --account NAME selects bed-<NAME>.token, so terminals never share a login.

Precedence: explicit --token-file, then --account, then the default bed.token. Names are limited
to letters, digits, '-' and '_' so a name cannot reach outside the runtime directory.
"""

import os
import stat

import pytest

from bed.tools import _token


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    d = tmp_path / "run"
    d.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(d))
    return d


@pytest.mark.unit
def test_account_selects_a_per_account_file(runtime):
    assert _token.token_path_for(account="alice") == str(runtime / "bed-alice.token")


@pytest.mark.unit
def test_no_account_uses_the_default_file(runtime):
    assert _token.token_path_for() == str(runtime / "bed.token")


@pytest.mark.unit
def test_explicit_token_file_beats_account(runtime, tmp_path):
    explicit = str(tmp_path / "mine.token")
    assert _token.token_path_for(account="alice", token_file=explicit) == explicit


@pytest.mark.unit
def test_two_accounts_never_share_a_file(runtime):
    a = _token.token_path_for(account="alice")
    b = _token.token_path_for(account="bob")
    assert a != b
    assert os.path.dirname(a) == os.path.dirname(b) == str(runtime)


@pytest.mark.unit
@pytest.mark.parametrize("name", ["alice", "Bob_2", "a-b", "x"])
def test_valid_account_names_are_accepted(name):
    assert _token.validate_account_name(name) == name


@pytest.mark.unit
@pytest.mark.parametrize("name", ["", "../etc", "a/b", "a b", "a.b", "a;rm", "x" * 65])
def test_unsafe_account_names_are_rejected(name):
    with pytest.raises(ValueError):
        _token.validate_account_name(name)


@pytest.mark.unit
def test_account_is_validated_before_building_the_path(runtime):
    with pytest.raises(ValueError):
        _token.token_path_for(account="../bob")


@pytest.mark.unit
def test_build_token_file_arg_registers_account_flag():
    import argparse

    p = argparse.ArgumentParser()
    _token.build_token_file_arg(p)
    args = p.parse_args(["--account", "alice"])
    assert args.account == "alice"
    assert args.token_file is None


@pytest.mark.unit
def test_ensure_token_file_arg_honours_account(runtime):
    import argparse

    args = argparse.Namespace(token_file=None, account="alice")
    _token.ensure_token_file_arg(args)
    assert args.token_file == str(runtime / "bed-alice.token")


@pytest.mark.unit
def test_per_account_dir_mode_stays_private(runtime):
    path = _token.token_path_for(account="alice")
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) & 0o077 == 0


@pytest.mark.unit
@pytest.mark.parametrize("tool", ["auth", "message", "bank"])
def test_each_token_tool_accepts_account(tool):
    import importlib
    import argparse

    mod = importlib.import_module(f"bed.tools.{tool}")
    parser = argparse.ArgumentParser()
    _token.build_token_file_arg(parser)  # the shared registration each tool calls
    assert parser.parse_args(["--account", "alice"]).account == "alice"
    src = (mod.__file__ and open(mod.__file__).read()) or ""
    assert "build_token_file_arg" in src, f"{tool} must register the shared token flags"
