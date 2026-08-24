"""Tests for bed.tools.auth (the standalone ``auth`` CLI script).

Covers:
- buildargs: registers --bed-* / --moniker / --password / --token /
  --token-file / --debug / --direct, plus the four subcommands
- _default_token_path: honours $XDG_RUNTIME_DIR, falls back to
  /tmp/bed-<uid>/bed.token, creates parent dir mode 0700 on demand
- _write_token_file: writes mode 0600, refuses loose parent dirs
- _read_token_file: returns "" for missing/empty, refuses loose perms
- _truncate_token_file: idempotent
- _resolve_token: --token > --token-file > "" precedence
- _ensure_token_file_arg: populates args.token_file with default
- auth_login happy path: client.login called once, echo + file write
- auth_login soft-failure rendering: bad_credentials, bed_unavailable,
  missing_credentials
- auth_login prompts via inputpassword when --password is absent
- auth_reconnect / auth_refresh / auth_revoke analogous coverage;
  revoke truncates the token file on success
- --direct guard: rejects with bundled error
- main_with_args catches KeyboardInterrupt / EOFError, surfaces
  BedNotReachable message
"""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------
# Helpers


def _make_args(
    *,
    subcommand: str = "login",
    moniker: str | None = "alice",
    password: str | None = "pw",
    token: str | None = None,
    token_file: str | None = None,
    direct: bool = False,
    **overrides: Any,
) -> argparse.Namespace:
    args = argparse.Namespace()
    args.subcommand = subcommand
    args.moniker = moniker
    args.password = password
    args.token = token
    args.token_file = token_file
    args.direct = direct
    args.bed_host = "localhost"
    args.bed_port = 8765
    args.bed_path = "/"
    args.bed_call_timeout = 5.0
    args.bed_probe_timeout = 0.25
    args.debug = False
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _import_tool():
    """Import bed.tools.auth fresh."""
    import importlib

    from bed.tools import auth as auth_mod

    return importlib.reload(auth_mod)


def _make_client_mock(**method_returns: Any):
    """Build a MagicMock that quacks like BedAuthServiceClient.

    Async methods default to benign return values; pass overrides.
    """
    client = MagicMock()
    client.login = AsyncMock(
        return_value=method_returns.get(
            "login",
            {
                "ok": True,
                "moniker": "alice",
                "is_sysop": False,
                "session_id": "sess-1",
                "token": "tok-1",
                "expires_at": "2030-01-01T00:00:00Z",
                "balance": 7,
            },
        )
    )
    client.reconnect = AsyncMock(
        return_value=method_returns.get(
            "reconnect",
            {
                "ok": True,
                "moniker": "alice",
                "is_sysop": False,
                "session_id": "sess-1",
                "token": "tok-rotated",
                "expires_at": "2030-01-01T00:15:00Z",
                "replayed": None,
            },
        )
    )
    client.refresh = AsyncMock(
        return_value=method_returns.get(
            "refresh",
            {
                "ok": True,
                "moniker": "alice",
                "is_sysop": False,
                "session_id": "sess-1",
                "token": "tok-rotated",
                "expires_at": "2030-01-01T00:15:00Z",
                "balance": 7,
            },
        )
    )
    client.revoke = AsyncMock(
        return_value=method_returns.get(
            "revoke",
            {"ok": True, "token": "tok-1", "code": None},
        )
    )
    return client


# ---------------------------------------------------------------------
# buildargs


def test_buildargs_registers_expected_flags():
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    a = parser.parse_args(["login"])
    assert a.subcommand == "login"
    assert a.moniker is None
    assert a.password is None
    assert a.token is None
    assert a.token_file is None
    assert a.bed_host == "localhost"
    assert a.bed_port == 8765
    assert a.bed_path == "/"
    assert a.bed_call_timeout == 5.0
    assert a.bed_probe_timeout == 0.25
    assert a.direct is False
    assert a.debug is False


def test_buildargs_parses_overrides_login():
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    a = parser.parse_args(
        [
            "--bed-port",
            "9999",
            "--debug",
            "login",
            "--moniker",
            "alice",
            "--password",
            "pw",
            "--token-file",
            "/tmp/tok",
        ]
    )
    assert a.subcommand == "login"
    assert a.moniker == "alice"
    assert a.password == "pw"
    assert a.token_file == "/tmp/tok"
    assert a.debug is True
    assert a.bed_port == 9999
    # --debug is a parent-parser flag now, so it lives on the top
    # level along with --bed-*.
    assert isinstance(a.debug, bool)


def test_buildargs_requires_subcommand():
    import pytest

    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_buildargs_all_subcommands():
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    for sub in ("login", "reconnect", "refresh", "revoke"):
        a = parser.parse_args([sub])
        assert a.subcommand == sub


# ---------------------------------------------------------------------
# _default_token_path


def test_default_token_path_uses_xdg_runtime_dir_when_set(monkeypatch):
    tool = _import_tool()
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    path = tool._default_token_path()
    assert path == "/run/user/1000/bed.token"


def test_default_token_path_falls_back_to_tmp_uid(monkeypatch):
    tool = _import_tool()
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    path = tool._default_token_path()
    expected = os.path.join(tempfile.gettempdir(), f"bed-{os.getuid()}", "bed.token")
    assert path == expected
    parent = os.path.dirname(path)
    assert os.path.isdir(parent)
    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o700


def test_default_token_path_creates_xdg_dir_mode_700(monkeypatch, tmp_path):
    tool = _import_tool()
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    path = tool._default_token_path()
    assert os.path.isdir(runtime)
    assert stat.S_IMODE(os.stat(runtime).st_mode) == 0o700
    assert path == str(runtime / "bed.token")


# ---------------------------------------------------------------------
# _ensure_parent_dir / _check_token_file_perms / write / read / truncate


def test_ensure_parent_dir_creates_with_strict_mode(tmp_path):
    tool = _import_tool()
    new_dir = tmp_path / "strict"
    new_file = new_dir / "tok"
    tool._ensure_parent_dir(str(new_file), mode=0o700)
    assert os.path.isdir(new_dir)
    assert stat.S_IMODE(os.stat(new_dir).st_mode) == 0o700


def test_ensure_parent_dir_refuses_loose_existing(tmp_path):
    tool = _import_tool()
    loose = tmp_path / "loose"
    os.makedirs(loose, mode=0o755)
    import pytest

    with pytest.raises(PermissionError):
        tool._ensure_parent_dir(str(loose / "tok"), mode=0o700)


def test_write_token_file_writes_mode_0600(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    tool._write_token_file(path, "secret-token")
    assert os.path.exists(path)
    with open(path) as f:
        assert f.read().strip() == "secret-token"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_write_token_file_refuses_loose_existing(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o644))
    import pytest

    with pytest.raises(PermissionError):
        tool._write_token_file(path, "x")


def test_read_token_file_returns_content(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("  mytoken  \n")
    os.chmod(path, 0o600)
    assert tool._read_token_file(path) == "mytoken"


def test_read_token_file_missing_returns_empty(tmp_path):
    tool = _import_tool()
    assert tool._read_token_file(str(tmp_path / "missing")) == ""


def test_read_token_file_refuses_loose_perms(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o644))
    import pytest

    with pytest.raises(PermissionError):
        tool._read_token_file(path)


def test_truncate_token_file_removes_when_present(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    tool._truncate_token_file(path)
    assert not os.path.exists(path)


def test_truncate_token_file_tolerates_missing(tmp_path):
    tool = _import_tool()
    tool._truncate_token_file(str(tmp_path / "missing"))


# ---------------------------------------------------------------------
# _resolve_token / _ensure_token_file_arg


def test_resolve_token_prefers_explicit_flag():
    tool = _import_tool()
    args = _make_args(token="from-flag", token_file=None)
    assert tool._resolve_token(args) == "from-flag"


def test_resolve_token_reads_file_when_flag_absent(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("from-file\n")
    os.chmod(path, 0o600)
    args = _make_args(token=None, token_file=path)
    assert tool._resolve_token(args) == "from-file"


def test_resolve_token_returns_empty_when_neither_set():
    tool = _import_tool()
    args = _make_args(token=None, token_file=None)
    assert tool._resolve_token(args) == ""


def test_ensure_token_file_arg_populates_default(monkeypatch):
    tool = _import_tool()
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    args = _make_args(token_file=None)
    tool._ensure_token_file_arg(args)
    assert args.token_file == "/run/user/1000/bed.token"


def test_ensure_token_file_arg_does_not_overwrite_explicit(tmp_path):
    tool = _import_tool()
    explicit = str(tmp_path / "explicit")
    args = _make_args(token_file=explicit)
    tool._ensure_token_file_arg(args)
    assert args.token_file == explicit


# ---------------------------------------------------------------------
# _check_token_response / _reject_malformed_token_response


def test_check_token_response_empty_reply_lists_all_three():
    tool = _import_tool()
    assert tool._check_token_response({}) == ["token", "session_id", "expires_at"]


def test_check_token_response_explicit_none_lists_all_three():
    tool = _import_tool()
    reply = {"token": None, "session_id": None, "expires_at": None}
    assert tool._check_token_response(reply) == ["token", "session_id", "expires_at"]


def test_check_token_response_only_token_missing():
    tool = _import_tool()
    reply = {"token": "", "session_id": "sess-1", "expires_at": "2030-01-01T00:00:00Z"}
    assert tool._check_token_response(reply) == ["token"]


def test_check_token_response_only_session_id_missing():
    tool = _import_tool()
    reply = {"token": "tok-1", "session_id": "", "expires_at": "2030-01-01T00:00:00Z"}
    assert tool._check_token_response(reply) == ["session_id"]


def test_check_token_response_only_expires_at_missing():
    tool = _import_tool()
    reply = {"token": "tok-1", "session_id": "sess-1", "expires_at": ""}
    assert tool._check_token_response(reply) == ["expires_at"]


def test_check_token_response_well_formed_returns_empty_list():
    tool = _import_tool()
    reply = {
        "ok": True,
        "token": "tok-1",
        "session_id": "sess-1",
        "expires_at": "2030-01-01T00:00:00Z",
        "moniker": "alice",
    }
    assert tool._check_token_response(reply) == []


def test_reject_malformed_token_response_well_formed_returns_false():
    tool = _import_tool()
    reply = {
        "ok": True,
        "token": "tok-1",
        "session_id": "sess-1",
        "expires_at": "2030-01-01T00:00:00Z",
    }
    with patch.object(tool.io, "echo") as echo:
        rejected = tool._reject_malformed_token_response(reply)
    assert rejected is False
    echo.assert_not_called()


def test_reject_malformed_token_response_empty_fields_emits_error():
    tool = _import_tool()
    reply = {
        "ok": True,
        "token": "",
        "session_id": "",
        "expires_at": "",
        "moniker": "alice",
    }
    with patch.object(tool.io, "echo") as echo:
        rejected = tool._reject_malformed_token_response(reply)
    assert rejected is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "malformed auth_result" in rendered
    assert "token" in rendered
    assert "session_id" in rendered
    assert "expires_at" in rendered


def test_reject_malformed_token_response_partial_lists_only_missing():
    tool = _import_tool()
    reply = {
        "ok": True,
        "token": "",
        "session_id": "sess-1",
        "expires_at": "2030-01-01T00:00:00Z",
    }
    with patch.object(tool.io, "echo") as echo:
        rejected = tool._reject_malformed_token_response(reply)
    assert rejected is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "malformed auth_result" in rendered
    assert "token" in rendered
    assert "session_id" not in rendered
    assert "expires_at" not in rendered


# ---------------------------------------------------------------------
# auth_login


def _patch_login_io(tool, **overrides):
    """Patch io.inputstring / inputpassword / echo to quiet defaults."""
    patches = [
        patch.object(tool.io, "inputstring", return_value=overrides.get("moniker_input", "")),
        patch.object(tool, "inputpassword", return_value=overrides.get("password_input", "")),
        patch.object(tool.io, "echo"),
    ]
    return patches


def test_auth_login_happy_path_writes_token_file(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    args = _make_args(moniker="alice", password="pw", token_file=path)
    client = _make_client_mock(login={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "sess-abc",
        "token": "tok-1",
        "expires_at": "2030-01-01T00:00:00Z",
        "balance": 7,
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool, "inputpassword", return_value="pw"), \
         patch.object(tool.io, "inputstring", return_value="alice"):
        ok = tool.auth_login(args)

    assert ok is True
    client.login.assert_awaited_once_with("alice", "pw")
    assert os.path.exists(path)
    with open(path) as f:
        assert f.read().strip() == "tok-1"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "alice" in rendered
    assert "tok-1" not in rendered  # full token is written to the file, not echoed
    assert path in rendered


def test_auth_login_prompts_moniker_and_password_when_missing(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    args = _make_args(moniker=None, password=None, token_file=path)
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"), \
         patch.object(tool, "inputpassword", return_value="prompted") as ipw, \
         patch.object(tool.io, "inputstring", return_value="prompted-moniker") as inm:
        ok = tool.auth_login(args)

    assert ok is True
    inm.assert_called_once()
    ipw.assert_called_once()
    client.login.assert_awaited_once_with("prompted-moniker", "prompted")


def test_auth_login_rejects_empty_moniker_locally(tmp_path):
    tool = _import_tool()
    args = _make_args(moniker=None, password="pw", token_file=str(tmp_path / "tok"))
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool, "inputpassword", return_value="pw"), \
         patch.object(tool.io, "inputstring", return_value="   "):
        ok = tool.auth_login(args)

    assert ok is False
    client.login.assert_not_called()
    assert any("moniker is required" in c.args[0] for c in echo.call_args_list)


def test_auth_login_rejects_empty_password_locally(tmp_path):
    tool = _import_tool()
    args = _make_args(moniker="alice", password=None, token_file=str(tmp_path / "tok"))
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool, "inputpassword", return_value="   "):
        ok = tool.auth_login(args)

    assert ok is False
    client.login.assert_not_called()
    assert any("password is required" in c.args[0] for c in echo.call_args_list)


def test_auth_login_propagates_bad_credentials(tmp_path):
    tool = _import_tool()
    args = _make_args(moniker="alice", password="wrong", token_file=str(tmp_path / "tok"))
    client = _make_client_mock(login={
        "ok": False,
        "code": "bad_credentials",
        "message": "Invalid moniker or password",
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool, "inputpassword", return_value="wrong"), \
         patch.object(tool.io, "inputstring", return_value="alice"):
        ok = tool.auth_login(args)

    assert ok is False
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "bad_credentials" in rendered
    assert "Invalid moniker or password" in rendered
    # Token file must not exist when login failed.
    assert not os.path.exists(args.token_file)


def test_auth_login_propagates_bed_unavailable(tmp_path):
    tool = _import_tool()
    args = _make_args(moniker="alice", password="pw", token_file=str(tmp_path / "tok"))
    client = _make_client_mock(login={
        "ok": False,
        "code": "bed_unavailable",
        "message": "ws down",
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool, "inputpassword", return_value="pw"), \
         patch.object(tool.io, "inputstring", return_value="alice"):
        ok = tool.auth_login(args)

    assert ok is False
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "bed_unavailable" in rendered
    assert "ws down" in rendered


def test_auth_login_rejects_malformed_auth_result_with_empty_fields(tmp_path):
    """Server replied ok=True but with empty token/session_id/expires_at.

    Regression for the --token-file bug: without validation, an empty
    token gets written to disk and the next ``auth reconnect`` then
    fails with a misleading ``missing_token`` error.
    """
    tool = _import_tool()
    path = str(tmp_path / "tok")
    args = _make_args(moniker="alice", password="pw", token_file=path)
    client = _make_client_mock(login={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "",
        "token": "",
        "expires_at": "",
        "balance": 0,
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool, "inputpassword", return_value="pw"), \
         patch.object(tool.io, "inputstring", return_value="alice"):
        ok = tool.auth_login(args)

    assert ok is False
    client.login.assert_awaited_once_with("alice", "pw")
    assert not os.path.exists(path), "empty-token file must not be written"
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "malformed auth_result" in rendered
    assert "token" in rendered
    assert "session_id" in rendered
    assert "expires_at" in rendered


def test_auth_login_rejects_malformed_auth_result_with_only_token_empty(tmp_path):
    """Same regression, but only ``token`` is empty (partial response)."""
    tool = _import_tool()
    path = str(tmp_path / "tok")
    args = _make_args(moniker="alice", password="pw", token_file=path)
    client = _make_client_mock(login={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "sess-1",
        "token": "",
        "expires_at": "2030-01-01T00:00:00Z",
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool, "inputpassword", return_value="pw"), \
         patch.object(tool.io, "inputstring", return_value="alice"):
        ok = tool.auth_login(args)

    assert ok is False
    assert not os.path.exists(path)
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "malformed auth_result" in rendered
    assert "token" in rendered
    assert "session_id" not in rendered  # only the missing field is named


# ---------------------------------------------------------------------
# auth_reconnect


def test_auth_reconnect_happy_path_writes_new_token(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("old-tok\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="reconnect", token=None, token_file=path)
    client = _make_client_mock(reconnect={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "sess-1",
        "token": "new-tok",
        "expires_at": "2030-01-01T00:15:00Z",
        "replayed": None,
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"):
        ok = tool.auth_reconnect(args)

    assert ok is True
    client.reconnect.assert_awaited_once_with("old-tok")
    with open(path) as f:
        assert f.read().strip() == "new-tok"


def test_auth_reconnect_prefers_explicit_token_flag(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("file-tok\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="reconnect", token="flag-tok", token_file=path)
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"):
        ok = tool.auth_reconnect(args)

    assert ok is True
    client.reconnect.assert_awaited_once_with("flag-tok")


def test_auth_reconnect_missing_token_local_short_circuit():
    tool = _import_tool()
    args = _make_args(subcommand="reconnect", token=None, token_file=None)
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_reconnect(args)

    assert ok is False
    client.reconnect.assert_not_called()
    assert any("missing_token" in c.args[0] for c in echo.call_args_list)


def test_auth_reconnect_propagates_forbidden():
    tool = _import_tool()
    args = _make_args(subcommand="reconnect", token="some-tok")
    client = _make_client_mock(reconnect={
        "ok": False,
        "code": "forbidden",
        "message": "Operation not permitted for this token",
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_reconnect(args)

    assert ok is False
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "forbidden" in rendered


def test_auth_reconnect_reports_replayed_pending(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("old\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="reconnect", token=None, token_file=path)
    client = _make_client_mock(reconnect={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "sess-1",
        "token": "new",
        "expires_at": "2030-01-01T00:15:00Z",
        "replayed": {"type": "io_push"},
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_reconnect(args)

    assert ok is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "replayed=yes" in rendered


def test_auth_reconnect_rejects_malformed_with_empty_fields(tmp_path):
    """Server replied ok=True but with empty token/session_id/expires_at.

    Regression for the --token-file bug on reconnect: without
    validation, the rotated ``new_token`` (``""``) overwrites the
    file via ``O_TRUNC`` and the next ``auth reconnect`` then fails
    with a misleading ``missing_token`` error. The CLI must refuse
    to write the file and emit the standard malformed error.
    """
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("old-tok\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="reconnect", token=None, token_file=path)
    client = _make_client_mock(reconnect={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "",
        "token": "",
        "expires_at": "",
        "replayed": None,
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_reconnect(args)

    assert ok is False
    client.reconnect.assert_awaited_once_with("old-tok")
    assert os.path.exists(path), "old token file must be preserved"
    with open(path) as f:
        assert f.read().strip() == "old-tok", (
            "must not overwrite a still-valid token with an empty string"
        )
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "malformed auth_result" in rendered
    assert "token" in rendered
    assert "session_id" in rendered
    assert "expires_at" in rendered


def test_auth_reconnect_rejects_malformed_with_only_token_empty(tmp_path):
    """Same regression, but only ``token`` is empty (partial response)."""
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("old-tok\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="reconnect", token=None, token_file=path)
    client = _make_client_mock(reconnect={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "sess-1",
        "token": "",
        "expires_at": "2030-01-01T00:15:00Z",
        "replayed": None,
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_reconnect(args)

    assert ok is False
    with open(path) as f:
        assert f.read().strip() == "old-tok"
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "malformed auth_result" in rendered
    assert "token" in rendered
    assert "session_id" not in rendered
    assert "expires_at" not in rendered


# ---------------------------------------------------------------------
# auth_refresh


def test_auth_refresh_happy_path_writes_new_token(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("old\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="refresh", token=None, token_file=path)
    client = _make_client_mock(refresh={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "sess-1",
        "token": "rotated",
        "expires_at": "2030-01-01T00:15:00Z",
        "balance": 7,
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"):
        ok = tool.auth_refresh(args)

    assert ok is True
    client.refresh.assert_awaited_once_with("old")
    with open(path) as f:
        assert f.read().strip() == "rotated"


def test_auth_refresh_missing_token_local_short_circuit():
    tool = _import_tool()
    args = _make_args(subcommand="refresh", token=None, token_file=None)
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_refresh(args)

    assert ok is False
    client.refresh.assert_not_called()
    assert any("missing_token" in c.args[0] for c in echo.call_args_list)


def test_auth_refresh_propagates_not_authenticated():
    tool = _import_tool()
    args = _make_args(subcommand="refresh", token="tok")
    client = _make_client_mock(refresh={
        "ok": False,
        "code": "not_authenticated",
        "message": "auth_refresh requires the original socket; use reconnect",
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_refresh(args)

    assert ok is False
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "not_authenticated" in rendered


def test_auth_refresh_rejects_malformed_with_empty_fields(tmp_path):
    """Server replied ok=True but with empty token/session_id/expires_at.

    Regression for the --token-file bug on refresh: without
    validation, the rotated ``new_token`` (``""``) overwrites the
    file via ``O_TRUNC`` and the next ``auth reconnect`` then fails
    with a misleading ``missing_token`` error. The CLI must refuse
    to write the file and emit the standard malformed error.
    """
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("old-tok\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="refresh", token=None, token_file=path)
    client = _make_client_mock(refresh={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "",
        "token": "",
        "expires_at": "",
        "balance": 7,
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_refresh(args)

    assert ok is False
    client.refresh.assert_awaited_once_with("old-tok")
    assert os.path.exists(path), "old token file must be preserved"
    with open(path) as f:
        assert f.read().strip() == "old-tok", (
            "must not overwrite a still-valid token with an empty string"
        )
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "malformed auth_result" in rendered
    assert "token" in rendered
    assert "session_id" in rendered
    assert "expires_at" in rendered


def test_auth_refresh_rejects_malformed_with_only_token_empty(tmp_path):
    """Same regression, but only ``token`` is empty (partial response)."""
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("old-tok\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="refresh", token=None, token_file=path)
    client = _make_client_mock(refresh={
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "sess-1",
        "token": "",
        "expires_at": "2030-01-01T00:15:00Z",
        "balance": 7,
    })
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_refresh(args)

    assert ok is False
    with open(path) as f:
        assert f.read().strip() == "old-tok"
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "malformed auth_result" in rendered
    assert "token" in rendered
    assert "session_id" not in rendered
    assert "expires_at" not in rendered


# ---------------------------------------------------------------------
# auth_revoke


def test_auth_revoke_happy_path_truncates_token_file(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("to-be-revoked\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="revoke", token=None, token_file=path)
    client = _make_client_mock(revoke={"ok": True, "token": "to-be-revoked", "code": None})
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_revoke(args)

    assert ok is True
    client.revoke.assert_awaited_once_with("to-be-revoked")
    assert not os.path.exists(path)
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "token revoked" in rendered


def test_auth_revoke_uses_explicit_token(tmp_path):
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("from-file\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="revoke", token="from-flag", token_file=path)
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"):
        ok = tool.auth_revoke(args)

    assert ok is True
    client.revoke.assert_awaited_once_with("from-flag")
    # Even when --token is given, the file is truncated (revoke is
    # destructive; the file is no longer authoritative).
    assert not os.path.exists(path)


def test_auth_revoke_missing_token_local_short_circuit():
    tool = _import_tool()
    args = _make_args(subcommand="revoke", token=None, token_file=None)
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_revoke(args)

    assert ok is False
    client.revoke.assert_not_called()
    assert any("missing_token" in c.args[0] for c in echo.call_args_list)


def test_auth_revoke_propagates_failure_does_not_truncate(tmp_path):
    """If the server says the revoke failed, we must NOT truncate
    the token file (a false-positive truncate could lock the
    operator out of a still-valid token)."""
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("still-good\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="revoke", token=None, token_file=path)
    client = _make_client_mock(revoke={"ok": False, "code": "bed_unavailable", "message": "down"})
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"):
        ok = tool.auth_revoke(args)

    assert ok is False
    assert os.path.exists(path)
    with open(path) as f:
        assert f.read().strip() == "still-good"


def test_auth_revoke_already_deleted_soft_failure(tmp_path):
    """Server reports ``token_revoked`` (token already gone from the
    store): the local token file is also unusable, so we truncate it
    so downstream tools (casino, etc.) don't keep trying to use it.
    """
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("tok\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="revoke", token=None, token_file=path)
    client = _make_client_mock(revoke={"ok": False, "code": "token_revoked", "message": "already gone"})
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_revoke(args)

    assert ok is False
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "token_revoked" in rendered
    assert "truncated" in rendered
    assert not os.path.exists(path)


def test_auth_revoke_transport_failure_keeps_token_file(tmp_path):
    """A transport-level failure (``bed_unavailable``) means we don't
    know whether the token is still valid on the server. The local file
    is left alone so a still-valid token isn't lost.
    """
    tool = _import_tool()
    path = str(tmp_path / "tok")
    with open(path, "w") as f:
        f.write("still-good\n")
    os.chmod(path, 0o600)
    args = _make_args(subcommand="revoke", token=None, token_file=path)
    client = _make_client_mock(revoke={"ok": False, "code": "bed_unavailable", "message": "down"})
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.auth_revoke(args)

    assert ok is False
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "bed_unavailable" in rendered
    assert "truncated" not in rendered
    assert os.path.exists(path)
    with open(path) as f:
        assert f.read().strip() == "still-good"


# ---------------------------------------------------------------------
# --direct guard and main_with_args routing


def test_direct_flag_rejected_at_startup():
    tool = _import_tool()
    args = _make_args(direct=True)
    with patch.object(tool.io, "echo") as echo, \
         patch.object(tool._routing, "select_backend") as sb:
        ok = tool.main_with_args(args)
    sb.assert_not_called()
    assert ok is False
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "--direct is unsupported" in rendered


def test_bed_not_reachable_renders_bundled_message():
    tool = _import_tool()
    args = _make_args(direct=False)
    with patch.object(tool.io, "echo") as echo, \
         patch.object(
             tool._routing, "select_backend",
             side_effect=tool._routing.BedNotReachable("nope.example", 9),
         ):
        ok = tool.main_with_args(args)
    assert ok is False
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "bed unreachable at nope.example:9" in rendered


def test_main_with_args_dispatches_login():
    tool = _import_tool()
    args = _make_args(subcommand="login", moniker="alice", password="pw")
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(tool, "auth_login", return_value=True) as al:
        ok = tool.main_with_args(args)
    assert ok is True
    al.assert_called_once_with(args)


def test_main_with_args_dispatches_reconnect():
    tool = _import_tool()
    args = _make_args(subcommand="reconnect", token="x")
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(tool, "auth_reconnect", return_value=True) as ar:
        ok = tool.main_with_args(args)
    assert ok is True
    ar.assert_called_once_with(args)


def test_main_with_args_dispatches_refresh():
    tool = _import_tool()
    args = _make_args(subcommand="refresh", token="x")
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(tool, "auth_refresh", return_value=True) as rf:
        ok = tool.main_with_args(args)
    assert ok is True
    rf.assert_called_once_with(args)


def test_main_with_args_dispatches_revoke():
    tool = _import_tool()
    args = _make_args(subcommand="revoke", token="x")
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(tool, "auth_revoke", return_value=True) as rv:
        ok = tool.main_with_args(args)
    assert ok is True
    rv.assert_called_once_with(args)


def test_main_with_args_unknown_subcommand():
    tool = _import_tool()
    args = _make_args(subcommand="bogus", moniker="alice", password="pw")
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.main_with_args(args)
    assert ok is False
    assert any("unknown subcommand" in c.args[0] for c in echo.call_args_list)


def test_main_with_args_keyboard_interrupt_swallowed():
    tool = _import_tool()
    args = _make_args(subcommand="login", moniker="alice", password="pw")
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(tool, "auth_login", side_effect=KeyboardInterrupt), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.main_with_args(args)
    assert ok is False
    assert any("*INTR*" in c.args[0] for c in echo.call_args_list)


def test_main_with_args_eof_swallowed():
    tool = _import_tool()
    args = _make_args(subcommand="login", moniker="alice", password="pw")
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(tool, "auth_login", side_effect=EOFError), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.main_with_args(args)
    assert ok is False
    assert any("*EOF*" in c.args[0] for c in echo.call_args_list)
