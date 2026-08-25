"""Regression pin for the prompt-color markup in bed's auth CLI.

The ``bed auth login`` prompts (``moniker:`` and ``password:``) are
read through ``bbsengine6.io.inputstring`` and
``bbsengine6.util.inputpassword`` respectively. After
``fix(bed): apply {var:promptcolor}/{var:inputcolor} to all CLI
interactive prompts`` (commit ``57a62d9``) those prompts wrap their
visible text in ``{var:promptcolor}…{var:inputcolor}`` so the
``bbsengine6.io`` screen-state pipeline can render the prompt
label and the user-typed input in their skin colors. A future
"simplify" PR that strips the markup (e.g. drops the ``var:``
qualifier, inlines the literal ANSI, or removes the closing
``{var:inputcolor}``) would silently regress the color scheme.

These tests pin the markup by capturing the prompt string passed
into the input helpers. They are NOT behavioral tests; they assert
only that the prompt-string argument contains the two required
markup substrings. Mirrors the mock-swap pattern in
``test_ping_tool.py`` for the ``bedping`` prompt migration.

Tests in this file:
- ``test_auth_login_promptcolor_on_moniker_prompt``: the
  ``io.inputstring`` call receives a prompt containing both
  ``{var:promptcolor}`` and ``{var:inputcolor}`` and ends in the
  input-color tag so keystrokes render in the right color.
- ``test_auth_login_promptcolor_on_password_prompt``: the
  ``inputpassword`` call receives a prompt containing both tags
  and ends in the input-color tag.
- ``test_auth_login_prompts_skip_when_args_supplied``: when
  ``args.moniker`` / ``args.password`` are pre-populated (e.g. via
  ``--moniker alice``), no input helpers fire; belt-and-braces
  check that the markup doesn't accidentally run on every code
  path.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch


def _make_args(
    *,
    moniker: str | None = None,
    password: str | None = None,
    token_file: str | None = None,
) -> argparse.Namespace:
    args = argparse.Namespace()
    args.subcommand = "login"
    args.moniker = moniker
    args.password = password
    args.token = None
    args.token_file = token_file or "/tmp/bed-test.tok"
    args.direct = False
    args.bed_host = "localhost"
    args.bed_port = 8765
    args.bed_path = "/"
    args.bed_call_timeout = 5.0
    args.bed_probe_timeout = 0.25
    args.debug = False
    return args


def _import_tool():
    """Import bed.tools.auth fresh."""
    import importlib

    from bed.tools import auth as auth_mod

    return importlib.reload(auth_mod)


def _make_client_mock():
    """Stub client that records its login() call and returns ok=True."""
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.login = AsyncMock(
        return_value={
            "ok": True,
            "moniker": "alice",
            "is_sysop": False,
            "session_id": "sess",
            "token": "tok",
            "expires_at": "2030-01-01T00:00:00Z",
        }
    )
    return client


def _strip_calls(mock_calls):
    """Flatten a mock's call args into positional strings.

    The first positional arg of ``io.inputstring`` /
    ``inputpassword`` is the prompt string. We collect them in
    call order so the test can verify both prompts fired and what
    markup they carried.
    """
    out = []
    for call in mock_calls:
        args, kwargs = call
        if args:
            out.append(args[0])
        elif "prompt" in kwargs:
            out.append(kwargs["prompt"])
    return out


def test_auth_login_promptcolor_on_moniker_prompt(tmp_path):
    """The moniker prompt must wrap its label in
    ``{var:promptcolor}`` and end in ``{var:inputcolor}``."""
    tool = _import_tool()
    args = _make_args(moniker=None, password="pw", token_file=str(tmp_path / "tok"))
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"), \
         patch.object(tool, "inputpassword", return_value="pw"), \
         patch.object(tool.io, "inputstring", return_value="alice") as inm:
        ok = tool.auth_login(args)
    assert ok is True

    captured = _strip_calls(inm.call_args_list)
    assert captured, "io.inputstring was never called"
    prompt = captured[0]
    assert "{var:promptcolor}" in prompt, (
        f"moniker prompt missing {{var:promptcolor}}: {prompt!r}"
    )
    assert "{var:inputcolor}" in prompt, (
        f"moniker prompt missing {{var:inputcolor}}: {prompt!r}"
    )
    assert prompt.rstrip().endswith("{var:inputcolor}"), (
        f"moniker prompt must end in {{var:inputcolor}} so user "
        f"keystrokes render in input color: {prompt!r}"
    )
    assert "moniker" in prompt.lower(), (
        f"moniker prompt should mention 'moniker': {prompt!r}"
    )


def test_auth_login_promptcolor_on_password_prompt(tmp_path):
    """The password prompt must wrap its label in
    ``{var:promptcolor}`` and end in ``{var:inputcolor}``."""
    tool = _import_tool()
    args = _make_args(moniker="alice", password=None, token_file=str(tmp_path / "tok"))
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"), \
         patch.object(tool.io, "inputstring", return_value="alice"), \
         patch.object(tool, "inputpassword", return_value="pw") as ipw:
        ok = tool.auth_login(args)
    assert ok is True

    captured = _strip_calls(ipw.call_args_list)
    assert captured, "inputpassword was never called"
    prompt = captured[0]
    assert "{var:promptcolor}" in prompt, (
        f"password prompt missing {{var:promptcolor}}: {prompt!r}"
    )
    assert "{var:inputcolor}" in prompt, (
        f"password prompt missing {{var:inputcolor}}: {prompt!r}"
    )
    assert prompt.rstrip().endswith("{var:inputcolor}"), (
        f"password prompt must end in {{var:inputcolor}} so masked "
        f"keystrokes render in input color: {prompt!r}"
    )
    assert "password" in prompt.lower(), (
        f"password prompt should mention 'password': {prompt!r}"
    )


def test_auth_login_prompts_skip_when_args_supplied(tmp_path):
    """When ``args.moniker`` and ``args.password`` are both
    pre-populated (e.g. via ``--moniker alice --password pw``), no
    input helpers fire. Belt-and-braces check that the markup
    isn't accidentally re-run on every code path."""
    tool = _import_tool()
    args = _make_args(moniker="alice", password="pw", token_file=str(tmp_path / "tok"))
    client = _make_client_mock()
    with patch.object(tool, "_auth_service", return_value=client), \
         patch.object(tool.io, "echo"), \
         patch.object(tool.io, "inputstring") as inm, \
         patch.object(tool, "inputpassword") as ipw:
        ok = tool.auth_login(args)
    assert ok is True
    assert inm.call_args_list == [], (
        "io.inputstring must not fire when args.moniker is pre-populated"
    )
    assert ipw.call_args_list == [], (
        "inputpassword must not fire when args.password is pre-populated"
    )


def test_collect_credentials_exposes_canonical_prompts(tmp_path):
    """``_collect_credentials`` is the seam that ``casino.auth.auth_prompt``
    reaches for so the prompt UX is byte-identical between
    ``bed auth login`` and the in-casino auth flow. This test pins
    the public-ish surface of that helper: it prompts with the same
    markup strings :func:`auth_login` uses, and the empty-input
    contract raises ``RuntimeError`` so non-CLI callers (e.g. the
    casino client) get a deterministic failure mode rather than a
    silent empty-credential round-trip.
    """
    tool = _import_tool()

    # Both fields absent: both prompts fire with the canonical
    # markup, and we get back the (moniker, password) pair the user
    # typed.
    args = _make_args(moniker=None, password=None, token_file=str(tmp_path / "tok"))
    with patch.object(tool.io, "echo"), \
         patch.object(tool.io, "inputstring", return_value="alice") as inm, \
         patch.object(tool, "inputpassword", return_value="pw") as ipw:
        moniker, password = tool._collect_credentials(args)
    assert (moniker, password) == ("alice", "pw")
    inm_prompts = [c.args[0] for c in inm.call_args_list if c.args]
    assert inm_prompts, "io.inputstring must fire for the moniker prompt"
    assert "{var:promptcolor}" in inm_prompts[0]
    assert "{var:inputcolor}" in inm_prompts[0]
    assert inm_prompts[0].rstrip().endswith("{var:inputcolor}")
    ipw_prompts = [c.args[0] for c in ipw.call_args_list if c.args]
    assert ipw_prompts, "inputpassword must fire for the password prompt"
    assert "{var:promptcolor}" in ipw_prompts[0]
    assert "{var:inputcolor}" in ipw_prompts[0]
    assert ipw_prompts[0].rstrip().endswith("{var:inputcolor}")

    # Empty moniker raises so the caller can decide how to surface
    # the failure (the CLI renders ``moniker is required`` and exits
    # non-zero; the casino prompt returns ``False``).
    args = _make_args(moniker=None, password="pw", token_file=str(tmp_path / "tok"))
    with patch.object(tool.io, "echo"), \
         patch.object(tool.io, "inputstring", return_value="") as inm, \
         patch.object(tool, "inputpassword") as ipw:
        import pytest

        with pytest.raises(RuntimeError):
            tool._collect_credentials(args)
    assert inm.call_args_list, "io.inputstring must fire for the moniker prompt"
    assert ipw.call_args_list == [], (
        "inputpassword must not fire when the moniker prompt was empty"
    )

    # Empty password raises the same way after the moniker is
    # accepted (no moniker prompt fired because ``args.moniker`` is
    # pre-populated).
    args = _make_args(moniker="alice", password=None, token_file=str(tmp_path / "tok"))
    with patch.object(tool.io, "echo"), \
         patch.object(tool.io, "inputstring", return_value="alice") as inm, \
         patch.object(tool, "inputpassword", return_value="") as ipw:
        import pytest

        with pytest.raises(RuntimeError):
            tool._collect_credentials(args)
    assert inm.call_args_list == [], (
        "io.inputstring must NOT fire when args.moniker is pre-populated"
    )
    assert ipw.call_args_list, "inputpassword must fire when args.password is unset"


def test_persist_token_writes_file_and_emits_diagnostic(tmp_path):
    """``_persist_token`` is the seam that ``casino.auth.auth_prompt``
    uses to materialise the freshly-minted bearer token to disk so
    the next ``casino`` invocation can skip prompting via
    ``_connect_with_token``. Pin the contract: the helper writes the
    token to ``args.token_file`` (mode 0600), emits the
    ``token written to ...`` line, and emits the
    ``auth_login.debug: token_file=...`` diagnostic so an operator
    can correlate a later ``token_revoked`` against the exact file
    bed just minted.
    """
    tool = _import_tool()

    token_path = str(tmp_path / "tok")
    args = _make_args(moniker="alice", password="pw", token_file=token_path)
    reply = {
        "ok": True,
        "moniker": "alice",
        "is_sysop": False,
        "session_id": "sess-1",
        "token": "tok-abc",
        "expires_at": "2030-01-01T00:00:00Z",
    }

    with patch.object(tool.io, "echo") as echo:
        ok = tool._persist_token(reply, args)

    assert ok is True
    # The file was written with the token, mode 0600.
    import os as _os
    import stat as _stat

    with open(token_path, "r", encoding="utf-8") as _f:
        contents = _f.read().strip()
    assert contents == "tok-abc"
    perms = _stat.S_IMODE(_os.stat(token_path).st_mode)
    assert perms == 0o600, f"token file mode {oct(perms)} != 0o600"

    # Two echo lines: the ``token written to ...`` line and the
    # ``auth_login.debug:`` diagnostic. The diagnostic carries the
    # 8-char SHA256 prefix so an operator log line
    # (``tok=<prefix>``) correlates with the server-side
    # ``AuthService.debug`` frame.
    echo_lines = [c.args[0] for c in echo.call_args_list if c.args]
    joined = "\n".join(echo_lines)
    assert any("token written to" in line for line in echo_lines), (
        f"missing 'token written to' line; got: {echo_lines!r}"
    )
    assert any("auth_login.debug:" in line for line in echo_lines), (
        f"missing 'auth_login.debug:' diagnostic; got: {echo_lines!r}"
    )
    assert "tok-abc" not in joined, (
        "diagnostic must not echo the raw token; it should carry "
        f"only an SHA256 prefix. got: {joined!r}"
    )
