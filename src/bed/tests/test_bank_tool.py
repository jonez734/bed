"""Tests for bed.tools.bank (the standalone ``bank`` CLI script).

Covers:
- buildargs: registers --databasename/--moniker/--sysop/--debug/--token-file
- _resolve_moniker: --moniker short-circuit, claim-derived
  ``_session_moniker``, getcurrentmoniker happy path, getcurrentmoniker
  returning None, and the pool is passed in
- _resolve_loginids: maps monikers to engine.__member.loginid via
  member.getbymoniker, tolerates pool failure, dedupes input
- bank_balance / bank_add / bank_remove / bank_transfer / bank_pending /
  bank_history / bank_approve / bank_reject / bank_list_all
  - each delegates to the right bbsengine6.bank.BankService method
  - each prints results via bbsengine6.io.echo
  - bank_history and bank_pending render by=<loginid> (with moniker fallback)
- bank_add / bank_remove / bank_transfer reject non-positive amounts
- bank_balance, bank_history, bank_list_all, bank_pending tolerate empty data
- main() short-circuits when _resolve_moniker returns None
- main() catches KeyboardInterrupt / EOFError cleanly
- TokenFileAuth: --token-file registration, _authenticate_ws
  (happy path, missing file, soft-failure codes, rotated-token
  write-back), _resolve_call_token (with caching), facade forwards
  the resolved token to BedBankServiceClient, direct mode skips
  authentication, main_with_args bed-mode flow
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest



# ---------------------------------------------------------------------
# Helpers


def _make_args(**overrides: Any) -> argparse.Namespace:
    args = argparse.Namespace()
    args.databasename = "test_db"
    args.databasehost = "localhost"
    args.databaseport = 5432
    args.databaseuser = "test_user"
    args.databasepassword = "test_pass"
    args.databaseschema = "engine"
    args.moniker = None
    args.sysop = False
    args.debug = False
    args.direct = True
    args.bed_host = "localhost"
    args.bed_port = 8765
    args.bed_path = "/"
    args.bed_call_timeout = 5.0
    args.bed_probe_timeout = 0.25
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def _make_bank_mock(**methods: Any) -> MagicMock:
    """Build a MagicMock that quacks like bbsengine6.bank.BankService.

    Methods default to benign return values. Pass overrides per-test.
    """
    bank = MagicMock()
    bank.get_balance = MagicMock(return_value=100)
    bank.add_funds = MagicMock(
        return_value={"success": True, "new_balance": 150, "message": "added"}
    )
    bank.remove_funds = MagicMock(
        return_value={"success": True, "new_balance": 75, "message": "removed"}
    )
    bank.transfer = MagicMock(
        return_value={"success": True, "message": "transferred"}
    )
    bank.get_pending_transfers = MagicMock(return_value=[])
    bank.approve_transfer = MagicMock(
        return_value={"success": True, "message": "approved"}
    )
    bank.reject_transfer = MagicMock(
        return_value={"success": True, "message": "rejected"}
    )
    bank.get_history = MagicMock(return_value=[])
    bank.list_all = MagicMock(return_value=[])
    for k, v in methods.items():
        setattr(bank, k, v)
    return bank


def _import_tool():
    """Import bed.tools.bank fresh."""
    import importlib

    from bed.tools import bank as bank_mod

    return importlib.reload(bank_mod)


def _make_inputchoice_mock(return_value: str = "Q"):
    """Build a side_effect for ``tool.io.inputchoice`` with ``help=`` support.

    Mirrors the KEY_F1 path in
    :func:`bbsengine6.io.inputchoice.inputchoice`: when a callable
    ``help=`` kwarg is supplied, the fake invokes it before returning
    ``return_value``. The returned ``calls`` list records every
    invocation as a dict so tests can assert on the kwargs the bank
    menu passed (e.g. that ``help=tool._render_bank_menu`` was wired).

    Returns:
        ``(side_effect, calls)`` tuple. ``calls`` is a list of dicts
        keyed by ``prompt``, ``valid``, ``default``, plus any kwargs
        forwarded by the bank menu.
    """
    calls: List[Dict[str, Any]] = []
    def _side_effect(prompt, valid, default, **kwargs):
        calls.append({
            "prompt": prompt,
            "valid": valid,
            "default": default,
            **kwargs,
        })
        help_cb = kwargs.get("help")
        if callable(help_cb):
            help_cb(**kwargs)
        return return_value
    return _side_effect, calls


# ---------------------------------------------------------------------
# buildargs


def test_buildargs_registers_expected_flags():
    """--databasename (via database.buildargs), --moniker, --sysop, --debug."""
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    a = parser.parse_args([])
    assert a.databasename == "zoid6"
    assert a.moniker is None
    assert a.sysop is False
    assert a.debug is False


def test_buildargs_parses_overrides():
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    a = parser.parse_args(
        ["--moniker", "alice", "--sysop", "--debug", "--databasename", "mydb"]
    )
    assert a.moniker == "alice"
    assert a.sysop is True
    assert a.debug is True
    assert a.databasename == "mydb"


# ---------------------------------------------------------------------
# _resolve_moniker


def test_resolve_moniker_short_circuits_on_explicit_moniker():
    """If args.moniker is set, the DB is not touched."""
    tool = _import_tool()
    args = _make_args(moniker="alice")
    with patch.object(tool.database, "getpool") as getpool, patch.object(
        tool.member, "getcurrentmoniker"
    ) as gcm:
        result = tool._resolve_moniker(args)
    assert result == "alice"
    getpool.assert_not_called()
    gcm.assert_not_called()


def test_resolve_moniker_passes_pool_to_getcurrentmoniker():
    """The pool built from args is what getcurrentmoniker receives.

    Regression test: bank.py used to call getcurrentmoniker without
    a pool, causing bbsengine6 to abort with
    'bbsengine.member.getcurrentmoniker.120: pool=None'.
    """
    tool = _import_tool()
    args = _make_args()
    fake_pool = MagicMock(name="pool")
    with patch.object(tool.database, "getpool", return_value=fake_pool) as getpool, \
         patch.object(tool.member, "getcurrentmoniker", return_value="bob") as gcm:
        result = tool._resolve_moniker(args)

    assert result == "bob"
    getpool.assert_called_once_with(args)
    gcm.assert_called_once_with(args, pool=fake_pool)


def test_resolve_moniker_prints_error_when_lookup_fails():
    """When getcurrentmoniker returns None we get None back AND an error echo."""
    tool = _import_tool()
    args = _make_args()
    with patch.object(tool.database, "getpool", return_value=MagicMock()), \
         patch.object(tool.member, "getcurrentmoniker", return_value=None), \
         patch.object(tool.io, "echo") as echo:
        result = tool._resolve_moniker(args)

    assert result is None
    echo.assert_called_once()
    assert "Could not determine current user" in echo.call_args[0][0]


# ---------------------------------------------------------------------
# _resolve_loginids


def test_resolve_loginids_returns_mapping_for_known_monikers():
    """_resolve_loginids queries member.getbymoniker for each unique moniker
    and returns {moniker: loginid} when the row has a non-empty loginid."""
    tool = _import_tool()
    args = _make_args()
    fake_pool = MagicMock(name="pool")

    def fake_getbymoniker(_args, moniker, fields="*", **_kwargs):
        return {
            "alice": {"loginid": "alice_os"},
            "bob": {"loginid": ""},
            "carol": {"loginid": "carol_os"},
        }.get(moniker)

    with patch.object(tool.database, "getpool", return_value=fake_pool), \
         patch.object(tool.member, "getbymoniker", side_effect=fake_getbymoniker) as gbm:
        result = tool._resolve_loginids(args, ["alice", "bob", "carol"])

    assert result == {"alice": "alice_os", "carol": "carol_os"}
    assert "bob" not in result
    assert sorted(c.args[1] for c in gbm.call_args_list) == [
        "alice", "bob", "carol",
    ]
    assert all(c.kwargs.get("fields") == "loginid" for c in gbm.call_args_list)


def test_resolve_loginids_dedupes_input():
    """A repeated moniker must not trigger a second getbymoniker call."""
    tool = _import_tool()
    args = _make_args()
    fake_pool = MagicMock(name="pool")

    with patch.object(tool.database, "getpool", return_value=fake_pool), \
         patch.object(
             tool.member,
             "getbymoniker",
             return_value={"loginid": "alice_os"},
         ) as gbm:
        result = tool._resolve_loginids(args, ["alice", "alice", "alice"])

    assert result == {"alice": "alice_os"}
    gbm.assert_called_once_with(args, "alice", fields="loginid", pool=fake_pool)


def test_resolve_loginids_skips_empty_monikers():
    """Empty-string monikers must not hit the DB."""
    tool = _import_tool()
    args = _make_args()
    fake_pool = MagicMock(name="pool")

    with patch.object(tool.database, "getpool", return_value=fake_pool), \
         patch.object(tool.member, "getbymoniker") as gbm:
        result = tool._resolve_loginids(args, ["", "", None])

    assert result == {}
    gbm.assert_not_called()


def test_resolve_loginids_handles_getbymoniker_failure():
    """If member.getbymoniker raises, the helper returns an empty dict
    so callers can degrade gracefully."""
    tool = _import_tool()
    args = _make_args()
    fake_pool = MagicMock(name="pool")

    with patch.object(tool.database, "getpool", return_value=fake_pool), \
         patch.object(
             tool.member,
             "getbymoniker",
             side_effect=RuntimeError("db down"),
         ):
        result = tool._resolve_loginids(args, ["alice"])

    assert result == {}


def test_resolve_loginids_handles_getpool_failure():
    """When database.getpool raises (e.g. invalid DSN in tests) we
    must still return an empty dict rather than crash the caller."""
    tool = _import_tool()
    args = _make_args()

    with patch.object(
        tool.database,
        "getpool",
        side_effect=ValueError("empty DSN"),
    ), patch.object(tool.member, "getbymoniker") as gbm:
        result = tool._resolve_loginids(args, ["alice"])

    assert result == {}
    gbm.assert_not_called()


# ---------------------------------------------------------------------
# bank_balance


def test_bank_balance_uses_service_and_echoes():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.bank_balance(args, "alice")

    assert ok is True
    bank.get_balance.assert_called_once_with("alice")
    echo.assert_called_once()
    assert "alice" in echo.call_args[0][0]
    assert "100" in echo.call_args[0][0]


# ---------------------------------------------------------------------
# bank_add


def test_bank_add_happy_path():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(
        add_funds=MagicMock(
            return_value={"success": True, "new_balance": 200, "message": "ok"}
        )
    )
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=100), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.bank_add(args, "alice")

    assert ok is True
    bank.add_funds.assert_called_once()
    assert bank.add_funds.call_args[0][0] == "alice"
    assert bank.add_funds.call_args[0][1] == 100
    echo.assert_called_once()
    assert "200" in echo.call_args[0][0]


def test_bank_add_rejects_none_amount():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=None), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.bank_add(args, "alice")

    assert ok is False
    bank.add_funds.assert_not_called()
    echo.assert_called_once()
    assert "Invalid amount" in echo.call_args[0][0]


def test_bank_add_rejects_zero_and_negative():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    for bad in (0, -1, -50):
        with patch.object(tool, "_bank_service", return_value=bank), \
             patch.object(tool.io, "inputinteger", return_value=bad), \
             patch.object(tool.io, "echo"):
            assert tool.bank_add(args, "alice") is False
    assert bank.add_funds.call_count == 0


def test_bank_add_propagates_failure():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(
        add_funds=MagicMock(return_value={"success": False, "message": "nope"})
    )
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=10), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.bank_add(args, "alice")

    assert ok is False
    echo.assert_called_once()
    assert "nope" in echo.call_args[0][0]


# ---------------------------------------------------------------------
# bank_remove


def test_bank_remove_happy_path():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(
        remove_funds=MagicMock(
            return_value={"success": True, "new_balance": 50, "message": "ok"}
        )
    )
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=50), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.bank_remove(args, "alice")

    assert ok is True
    bank.remove_funds.assert_called_once_with("alice", 50, transaction_type="debit")
    assert "50" in echo.call_args[0][0]


def test_bank_remove_rejects_invalid_amount():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=0), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_remove(args, "alice") is False
    bank.remove_funds.assert_not_called()
    assert "Invalid amount" in echo.call_args[0][0]


# ---------------------------------------------------------------------
# bank_transfer


def test_bank_transfer_happy_path():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(
        transfer=MagicMock(return_value={"success": True, "message": "xfer ok"})
    )
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=25), \
         patch.object(tool.io, "inputstring", return_value="bob"), \
         patch.object(tool.io, "echo") as echo:
        ok = tool.bank_transfer(args, "alice")

    assert ok is True
    bank.transfer.assert_called_once_with("alice", "bob", 25, "alice")
    assert "xfer ok" in echo.call_args[0][0]


def test_bank_transfer_rejects_empty_to_moniker():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputstring", return_value=""), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_transfer(args, "alice") is False
    bank.transfer.assert_not_called()
    assert "No moniker entered" in echo.call_args[0][0]


def test_bank_transfer_rejects_invalid_amount():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputstring", return_value="bob"), \
         patch.object(tool.io, "inputinteger", return_value=-1), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_transfer(args, "alice") is False
    bank.transfer.assert_not_called()
    assert "Invalid amount" in echo.call_args[0][0]


# ---------------------------------------------------------------------
# bank_pending


def test_bank_pending_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(get_pending_transfers=MagicMock(return_value=[]))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_pending(args, "alice") is True
    bank.get_pending_transfers.assert_called_once_with("alice", is_sysop=False)
    assert any("No pending" in c.args[0] for c in echo.call_args_list)


def test_bank_pending_passes_sysop_flag():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(
        get_pending_transfers=MagicMock(
            return_value=[
                {
                    "id": 1,
                    "from_moniker": "a",
                    "to_moniker": "b",
                    "amount": 10,
                    "requestedby": "a",
                    "requestedat": "2026-08-04",
                }
            ]
        )
    )
    # Skip the real DB-backed loginid lookup. The mocked rows don't
    # exercise that code path; without this patch
    # ``_resolve_loginids`` calls ``database.getpool`` which blocks
    # for ``psycopg_pool``'s default 5-second timeout before
    # raising -- turning these fast unit tests into 5s hangs.
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool, "_resolve_loginids", return_value={}), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_pending(args, "alice", is_sysop=True) is True
    bank.get_pending_transfers.assert_called_once_with("alice", is_sysop=True)
    # at least one echo call rendered a row
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "1" in rendered
    assert "a -> b" in rendered


def test_bank_pending_renders_loginid_for_known_actor():
    """When _resolve_loginids returns a loginid for the requester, that
    value is what appears in the ``by=`` field, not the raw moniker."""
    tool = _import_tool()
    args = _make_args()
    rows = [
        {
            "id": 1,
            "from_moniker": "alice",
            "to_moniker": "bob",
            "amount": 10,
            "requestedby": "alice",
            "requestedat": "2026-08-04",
        }
    ]
    bank = _make_bank_mock(get_pending_transfers=MagicMock(return_value=rows))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool, "_resolve_loginids", return_value={"alice": "alice_os"}), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_pending(args, "alice") is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "by=alice_os" in rendered
    # The line ends in `by=<value>  at=<date>`, so the fallback marker
    # would be `by=alice  at=`.
    assert "by=alice  at=" not in rendered


def test_bank_pending_falls_back_to_moniker_when_loginid_missing():
    """If _resolve_loginids returns no entry for the requester, the
    raw moniker is used as the ``by=`` value."""
    tool = _import_tool()
    args = _make_args()
    rows = [
        {
            "id": 1,
            "from_moniker": "alice",
            "to_moniker": "bob",
            "amount": 10,
            "requestedby": "alice",
            "requestedat": "2026-08-04",
        }
    ]
    bank = _make_bank_mock(get_pending_transfers=MagicMock(return_value=rows))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool, "_resolve_loginids", return_value={}), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_pending(args, "alice") is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "by=alice" in rendered


# ---------------------------------------------------------------------
# bank_approve / bank_reject


def test_bank_approve_happy_path():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(
        approve_transfer=MagicMock(
            return_value={"success": True, "message": "approved #5"}
        )
    )
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=5), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_approve(args, "alice") is True
    bank.approve_transfer.assert_called_once_with(5, "alice")
    assert "approved #5" in echo.call_args[0][0]


def test_bank_approve_rejects_none_id():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=None), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_approve(args, "alice") is False
    bank.approve_transfer.assert_not_called()
    assert "Invalid ID" in echo.call_args[0][0]


def test_bank_reject_happy_path():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(
        reject_transfer=MagicMock(
            return_value={"success": True, "message": "rejected #3"}
        )
    )
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=3), \
         patch.object(tool.io, "echo"):
        assert tool.bank_reject(args, "alice") is True
    bank.reject_transfer.assert_called_once_with(3, "alice")


def test_bank_reject_rejects_none_id():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=None), \
         patch.object(tool.io, "echo"):
        assert tool.bank_reject(args, "alice") is False
    bank.reject_transfer.assert_not_called()


# ---------------------------------------------------------------------
# bank_history


def test_bank_history_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(get_history=MagicMock(return_value=[]))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_history(args, "alice") is True
    bank.get_history.assert_called_once_with("alice")
    assert any("No transactions" in c.args[0] for c in echo.call_args_list)


def test_bank_history_renders_rows():
    tool = _import_tool()
    args = _make_args()
    rows: List[Dict[str, Any]] = [
        {
            "id": 7,
            "transactiontype": "credit",
            "amount": 50,
            "description": "payday",
            "membermoniker": "sysop",
            "dateposted": "2026-08-04",
        }
    ]
    bank = _make_bank_mock(get_history=MagicMock(return_value=rows))
    # Skip the real DB-backed loginid lookup. See
    # ``test_bank_pending_passes_sysop_flag`` for the rationale --
    # ``_resolve_loginids`` would otherwise block on
    # ``psycopg_pool``'s 5-second timeout trying to reach a
    # non-existent PostgreSQL instance.
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool, "_resolve_loginids", return_value={}), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_history(args, "alice") is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "7" in rendered
    assert "credit" in rendered
    assert "50" in rendered


def test_bank_history_renders_loginid_for_known_actor():
    """When _resolve_loginids returns a loginid for the actor, that
    value replaces the moniker in the ``by=`` field of every row."""
    tool = _import_tool()
    args = _make_args()
    rows = [
        {
            "id": 7,
            "transactiontype": "credit",
            "amount": 50,
            "description": "payday",
            "membermoniker": "sysop",
            "dateposted": "2026-08-04",
        },
        {
            "id": 8,
            "transactiontype": "debit",
            "amount": 5,
            "description": "snack",
            "membermoniker": "alice",
            "dateposted": "2026-08-04",
        },
    ]
    bank = _make_bank_mock(get_history=MagicMock(return_value=rows))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(
             tool,
             "_resolve_loginids",
             return_value={"sysop": "jam", "alice": "alice_os"},
         ), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_history(args, "bob") is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "by=jam" in rendered
    assert "by=alice_os" in rendered
    # Fallback markers would appear as `by=<moniker>  at=<date>`.
    assert "by=sysop  at=" not in rendered
    assert "by=alice  at=" not in rendered


def test_bank_history_falls_back_to_moniker_when_loginid_missing():
    """If _resolve_loginids returns no entry for the actor, the
    raw moniker is rendered as ``by=<moniker>``."""
    tool = _import_tool()
    args = _make_args()
    rows = [
        {
            "id": 7,
            "transactiontype": "credit",
            "amount": 50,
            "description": "payday",
            "membermoniker": "sysop",
            "dateposted": "2026-08-04",
        }
    ]
    bank = _make_bank_mock(get_history=MagicMock(return_value=rows))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool, "_resolve_loginids", return_value={}), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_history(args, "alice") is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "by=sysop" in rendered


def test_bank_history_partial_loginid_lookup_keeps_unknown_moniker():
    """A row whose actor is missing from the lookup falls back to its
    moniker; other rows still show their loginid."""
    tool = _import_tool()
    args = _make_args()
    rows = [
        {
            "id": 7,
            "transactiontype": "credit",
            "amount": 50,
            "description": "payday",
            "membermoniker": "sysop",
            "dateposted": "2026-08-04",
        },
        {
            "id": 8,
            "transactiontype": "debit",
            "amount": 5,
            "description": "snack",
            "membermoniker": "ghost",
            "dateposted": "2026-08-04",
        },
    ]
    bank = _make_bank_mock(get_history=MagicMock(return_value=rows))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(
             tool,
             "_resolve_loginids",
             return_value={"sysop": "jam"},  # "ghost" missing
         ), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_history(args, "alice") is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "by=jam" in rendered
    assert "by=ghost" in rendered


# ---------------------------------------------------------------------
# bank_list_all


def test_bank_list_all_empty():
    tool = _import_tool()
    args = _make_args(sysop=True)
    bank = _make_bank_mock(list_all=MagicMock(return_value=[]))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_list_all(args, "alice") is True
    bank.list_all.assert_called_once_with()
    assert any("No accounts" in c.args[0] for c in echo.call_args_list)


def test_bank_list_all_renders_rows():
    tool = _import_tool()
    args = _make_args(sysop=True)
    rows = [{"moniker": "alice", "balance": 100}, {"moniker": "bob", "balance": 50}]
    bank = _make_bank_mock(list_all=MagicMock(return_value=rows))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_list_all(args, "alice") is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "alice" in rendered
    assert "100" in rendered
    assert "bob" in rendered
    assert "50" in rendered


# ---------------------------------------------------------------------
# main()


def test_main_returns_when_moniker_unresolved():
    """When _resolve_moniker returns None, main() exits without entering the menu."""
    tool = _import_tool()
    args = _make_args(moniker=None)
    with patch.object(tool, "_resolve_moniker", return_value=None) as rm, \
         patch.object(tool.io, "inputchoice") as ic:
        rc = tool.main_with_args(args)
    assert rc is None
    rm.assert_called_once_with(args)
    ic.assert_not_called()


def test_main_quit_exits_cleanly():
    """Pressing 'Q' should leave the menu loop immediately."""
    tool = _import_tool()
    args = _make_args(moniker="alice")
    with patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", return_value="Q"), \
         patch.object(tool, "_bank_service") as bs, \
         patch.object(tool.bottombar, "setbottombar"), \
         patch.object(tool.bbsengine6_screen, "init"), \
         patch.object(tool.io, "echo"):
        tool.main_with_args(args)
    bs.assert_not_called()


def test_menu_inputchoice_help_kwarg_wired_and_outputs_menu():
    """The bank menu passes ``help=_render_bank_menu`` to ``io.inputchoice``,
    and invoking that help (simulating KEY_F1) reprints the menu options.

    The mock invokes the help callable on every inputchoice call --
    matching the real inputchoice's KEY_F1 path. With the menu loop
    iterating exactly once (mock returns ``Q``), the menu is rendered
    twice: once by the explicit ``_render_bank_menu(args=args)`` call
    above the loop, and once by the help kwarg invocation inside the
    mock. Each description must therefore appear in the captured
    ``io.echo`` calls at least twice.
    """
    tool = _import_tool()
    args = _make_args(moniker="alice")

    fake, calls = _make_inputchoice_mock("Q")
    with patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", side_effect=fake), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool.bottombar, "setbottombar"), \
         patch.object(tool.bbsengine6_screen, "init"):
        tool.main_with_args(args)

    # Wiring assertion: bank menu forwarded help=_render_bank_menu.
    assert calls, "io.inputchoice was not called"
    assert calls[0].get("help") is tool._render_bank_menu

    # Behavior assertion: each description appears >= 2 times.
    # Once from the initial _render_bank_menu(args=args) call above
    # the loop, and once from the help kwarg invocation inside the
    # mock (simulating KEY_F1).
    echo_blob = "\n".join(str(c) for c in echo.call_args_list)
    for desc in (
        "show current balance",
        "credit funds to this account",
        "debit funds from this account",
        "transfer funds to another member",
        "show pending transfers",
        "show transaction history",
        "list every account",
        "quit the bank menu",
    ):
        assert echo_blob.count(desc) >= 2, (
            f"description {desc!r} printed {echo_blob.count(desc)} times; "
            f"expected >= 2 (initial render + F1 help)"
        )


def test_main_keyboard_interrupt_swallowed():
    """Ctrl-C in the menu loop is caught and does not propagate."""
    tool = _import_tool()
    args = _make_args(moniker="alice")
    with patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", side_effect=KeyboardInterrupt), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool.bottombar, "setbottombar"), \
         patch.object(tool.bbsengine6_screen, "init"):
        tool.main_with_args(args)
    assert any("*INTR*" in c.args[0] for c in echo.call_args_list)


def test_main_eof_swallowed():
    tool = _import_tool()
    args = _make_args(moniker="alice")
    with patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", side_effect=EOFError), \
         patch.object(tool.io, "echo") as echo, \
         patch.object(tool.bottombar, "setbottombar"), \
         patch.object(tool.bbsengine6_screen, "init"):
        tool.main_with_args(args)
    assert any("*EOF*" in c.args[0] for c in echo.call_args_list)


# ---------------------------------------------------------------------
# routing layer


def test_buildargs_registers_bed_and_direct_flags():
    """--bed-host / --bed-port / --direct all parse."""
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    a = parser.parse_args(
        ["--bed-host", "b", "--bed-port", "9999", "--direct"]
    )
    assert a.bed_host == "b"
    assert a.bed_port == 9999
    assert a.direct is True


def test_buildargs_database_args_hidden_from_help():
    """Database args still parse (legacy) but are suppressed in --help."""
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    a = parser.parse_args(["--databasename", "mydb"])
    assert a.databasename == "mydb"
    help_text = parser.format_help()
    assert "--databasename" not in help_text


def test_bank_service_returns_direct_for_default_backend():
    """When args._backend is unset or 'direct', return the local BankService."""
    from bbsengine6.bank import BankService

    tool = _import_tool()
    args = _make_args()
    svc = tool._bank_service(args)
    assert isinstance(svc, BankService)


def test_bank_service_returns_facade_when_backend_is_bed():
    """When args._backend == 'bed', return the sync _BedBankFacade."""
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    svc = tool._bank_service(args)
    assert isinstance(svc, tool._BedBankFacade)


def test_main_with_args_calls_routing_and_stashes_backend():
    """select_backend is called and its result lands on args._backend."""
    tool = _import_tool()
    args = _make_args(moniker="alice")
    with patch.object(tool._routing, "select_backend", return_value="bed") as sb, \
         patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", return_value="Q"):
        tool.main_with_args(args)
    sb.assert_called_once_with(args)
    assert args._backend == "bed"


def test_main_with_args_unreachable_bed_echoes_error_and_returns():
    """When bed is unreachable, exit with the operator-facing message."""
    tool = _import_tool()
    args = _make_args(moniker="alice")
    with patch.object(
        tool._routing, "select_backend",
        side_effect=tool._routing.BedNotReachable("h", 9),
    ), patch.object(tool.io, "echo") as echo:
        rc = tool.main_with_args(args)
    assert rc is None
    assert any(
        "bed unreachable at h:9" in c.args[0] for c in echo.call_args_list
    )
    assert any(
        "rerun with --direct" in c.args[0] for c in echo.call_args_list
    )


# ---------------------------------------------------------------------
# _BedBankFacade — sync bridge over BedBankServiceClient


def test_facade_uses_get_bed_connection_singleton():
    """The facade shares the module-level BedConnection singleton."""
    from bed.client.singleton import get_bed_connection

    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    assert facade._client._conn is get_bed_connection(args)


def test_facade_get_balance_returns_int_on_ok():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade._client, "get_balance",
        new=_async_return({"ok": True, "balance": 250}),
    ):
        assert facade.get_balance("alice") == 250


def test_facade_get_balance_returns_zero_on_soft_failure():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade._client, "get_balance",
        new=_async_return({"ok": False, "code": "bed_unavailable", "message": "x"}),
    ):
        assert facade.get_balance("alice") == 0


def test_facade_add_funds_translates_wire_to_direct_shape():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade._client, "add_funds",
        new=_async_return({"ok": True, "new_balance": 350, "amount": 50}),
    ):
        result = facade.add_funds("alice", 50, transaction_type="credit")
    assert result["success"] is True
    assert result["new_balance"] == 350
    assert "50" in result["message"]


def test_facade_remove_funds_translates_wire_to_direct_shape():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade._client, "remove_funds",
        new=_async_return({"ok": True, "new_balance": 75, "amount": 25}),
    ):
        result = facade.remove_funds("alice", 25, transaction_type="debit")
    assert result["success"] is True
    assert result["new_balance"] == 75


def test_facade_transfer_translates_wire_to_direct_shape():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade._client, "transfer",
        new=_async_return(
            {"ok": True, "transfer_id": 7, "message": "queued"}
        ),
    ):
        result = facade.transfer("alice", "bob", 25, "alice")
    assert result["success"] is True
    assert result["transfer_id"] == 7


def test_facade_transfer_soft_failure_returns_success_false():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade._client, "transfer",
        new=_async_return(
            {"ok": False, "code": "bed_unavailable", "message": "down"}
        ),
    ):
        result = facade.transfer("alice", "bob", 25, "alice")
    assert result["success"] is False
    assert result["message"] == "down"


def test_facade_get_pending_transfers_returns_list():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    rows = [{"id": 1, "from_moniker": "a", "to_moniker": "b"}]
    with patch.object(
        facade._client, "get_pending_transfers",
        new=_async_return({"ok": True, "transfers": rows}),
    ):
        assert facade.get_pending_transfers("alice", is_sysop=True) == rows


def test_facade_approve_transfer_translates_wire_to_direct_shape():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade._client, "approve_transfer",
        new=_async_return(
            {"ok": True, "transfer_id": 7, "from_balance": 50, "to_balance": 80}
        ),
    ):
        result = facade.approve_transfer(7, "alice")
    assert result["success"] is True
    assert result["from_balance"] == 50
    assert result["to_balance"] == 80


def test_facade_reject_transfer_translates_wire_to_direct_shape():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade._client, "reject_transfer",
        new=_async_return({"ok": True, "transfer_id": 7}),
    ):
        result = facade.reject_transfer(7, "alice")
    assert result["success"] is True
    assert result["transfer_id"] == 7


def test_facade_get_history_returns_list():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    rows = [{"id": 1, "amount": 50}]
    with patch.object(
        facade._client, "get_history",
        new=_async_return({"ok": True, "transactions": rows}),
    ):
        assert facade.get_history("alice") == rows


def test_facade_list_all_returns_list():
    tool = _import_tool()
    args = _make_args()
    facade = tool._BedBankFacade(args)
    rows = [{"moniker": "alice", "balance": 100}]
    with patch.object(
        facade._client, "list_all",
        new=_async_return({"ok": True, "accounts": rows}),
    ):
        assert facade.list_all() == rows


# ---------------------------------------------------------------------
# end-to-end: bed-mode tool functions delegate through the facade


def test_bank_balance_in_bed_mode_uses_facade():
    """In bed mode, bank_balance delegates to the facade's get_balance."""
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    with patch.object(facade, "get_balance", return_value=999) as gb, \
         patch.object(tool.io, "echo") as echo:
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_balance(args, "alice") is True
    gb.assert_called_once_with("alice")
    rendered = " ".join(c.args[0] for c in echo.call_args_list)
    assert "999" in rendered


def test_bank_add_in_bed_mode_uses_facade():
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade, "add_funds",
        return_value={"success": True, "new_balance": 200, "message": "ok"},
    ), patch.object(tool.io, "inputinteger", return_value=100), \
         patch.object(tool.io, "echo") as echo:
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_add(args, "alice") is True
    rendered = " ".join(c.args[0] for c in echo.call_args_list)
    assert "200" in rendered


def test_bank_remove_in_bed_mode_uses_facade():
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade, "remove_funds",
        return_value={"success": True, "new_balance": 50, "message": "ok"},
    ), patch.object(tool.io, "inputinteger", return_value=50), \
         patch.object(tool.io, "echo") as echo:
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_remove(args, "alice") is True
    rendered = " ".join(c.args[0] for c in echo.call_args_list)
    assert "50" in rendered


def test_bank_transfer_in_bed_mode_uses_facade():
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade, "transfer",
        return_value={"success": True, "message": "xfer ok"},
    ) as transfer_mock, \
         patch.object(tool.io, "inputinteger", return_value=25), \
         patch.object(tool.io, "inputstring", return_value="bob"), \
         patch.object(tool.io, "echo"):
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_transfer(args, "alice") is True
    transfer_mock.assert_called_once_with("alice", "bob", 25, "alice")


def test_bank_pending_in_bed_mode_uses_facade():
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    rows = [
        {
            "id": 1,
            "from_moniker": "a",
            "to_moniker": "b",
            "amount": 10,
            "requestedby": "a",
            "requestedat": "2026-08-04",
        }
    ]
    # Skip the real DB-backed loginid lookup. See
    # ``test_bank_pending_passes_sysop_flag`` for the rationale --
    # ``_resolve_loginids`` would otherwise block on
    # ``psycopg_pool``'s 5-second timeout trying to reach a
    # non-existent PostgreSQL instance.
    with patch.object(
        facade, "get_pending_transfers", return_value=rows,
    ) as pending_mock, \
         patch.object(tool, "_resolve_loginids", return_value={}), \
         patch.object(tool.io, "echo"):
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_pending(args, "alice", is_sysop=True) is True
    pending_mock.assert_called_once_with("alice", is_sysop=True)


def test_bank_approve_in_bed_mode_uses_facade():
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade, "approve_transfer",
        return_value={"success": True, "message": "ok"},
    ) as approve_mock, \
         patch.object(tool.io, "inputinteger", return_value=5), \
         patch.object(tool.io, "echo"):
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_approve(args, "alice") is True
    approve_mock.assert_called_once_with(5, "alice")


def test_bank_reject_in_bed_mode_uses_facade():
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    with patch.object(
        facade, "reject_transfer",
        return_value={"success": True, "message": "ok"},
    ) as reject_mock, \
         patch.object(tool.io, "inputinteger", return_value=5), \
         patch.object(tool.io, "echo"):
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_reject(args, "alice") is True
    reject_mock.assert_called_once_with(5, "alice")


def test_bank_history_in_bed_mode_uses_facade():
    tool = _import_tool()
    args = _make_args()
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    with patch.object(facade, "get_history", return_value=[]) as history_mock, \
         patch.object(tool.io, "echo"):
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_history(args, "alice") is True
    history_mock.assert_called_once_with("alice")


def test_bank_list_all_in_bed_mode_uses_facade():
    tool = _import_tool()
    args = _make_args(sysop=True)
    args._backend = "bed"
    facade = tool._BedBankFacade(args)
    rows = [{"moniker": "alice", "balance": 100}]
    with patch.object(facade, "list_all", return_value=rows) as list_mock, \
         patch.object(tool.io, "echo") as echo:
        with patch.object(tool, "_bank_service", return_value=facade):
            assert tool.bank_list_all(args, "alice") is True
    list_mock.assert_called_once_with()
    rendered = " ".join(c.args[0] for c in echo.call_args_list)
    assert "alice" in rendered
    assert "100" in rendered


# ---------------------------------------------------------------------
# helpers


def _async_return(value):
    """Build an AsyncMock that returns ``value`` when awaited."""

    async def _coro(*args, **kwargs):
        return value

    return MagicMock(side_effect=_coro)


# ---------------------------------------------------------------------
# access() gating -- bed.tools.bank delegates authorization to
# bbsengine6.bank.access(). These tests pin that delegation.
#
# Design note: every bank_X CLI subcommand operates on the calling
# member's own account (the ``moniker`` arg is the RESOLVED user,
# used as both session and target). The only realistic denial cases
# are (a) bank_list_all without --sysop, and (b) any subcommand
# invoked with an empty/unresolved moniker (e.g. directly without
# going through _resolve_moniker). Helpers (_check_access,
# _make_session) are tested directly so the policy plumbing is
# pinned even though the user-facing subcommands rarely exercise
# the deny branch.


def test_bank_list_all_denies_non_sysop():
    """bank_list_all is sysop-only via bbsengine6.bank.access."""
    tool = _import_tool()
    args = _make_args()  # sysop=False
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo"):
        assert tool.bank_list_all(args, "alice") is False
    bank.list_all.assert_not_called()


def test_bank_list_all_allows_sysop():
    """--sysop satisfies the list_all sysop-only rule."""
    tool = _import_tool()
    args = _make_args(sysop=True)
    bank = _make_bank_mock(list_all=MagicMock(return_value=[]))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo"):
        assert tool.bank_list_all(args, "alice") is True
    bank.list_all.assert_called_once_with()


def test_bank_balance_denies_when_session_moniker_empty():
    """If session_moniker is empty (caller never resolved a user),
    access() denies and the service is never called."""
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(get_balance=MagicMock(return_value=42))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo"):
        assert tool.bank_balance(args, "") is False
    bank.get_balance.assert_not_called()


def test_bank_history_denies_when_session_moniker_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo"):
        assert tool.bank_history(args, "") is False
    bank.get_history.assert_not_called()


def test_bank_pending_denies_when_session_moniker_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo"):
        assert tool.bank_pending(args, "") is False
    bank.get_pending_transfers.assert_not_called()


def test_bank_add_denies_when_session_moniker_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=10), \
         patch.object(tool.io, "echo"):
        assert tool.bank_add(args, "") is False
    bank.add_funds.assert_not_called()


def test_bank_remove_denies_when_session_moniker_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=10), \
         patch.object(tool.io, "echo"):
        assert tool.bank_remove(args, "") is False
    bank.remove_funds.assert_not_called()


def test_bank_transfer_denies_when_session_moniker_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputstring", return_value="bob"), \
         patch.object(tool.io, "inputinteger", return_value=5), \
         patch.object(tool.io, "echo"):
        assert tool.bank_transfer(args, "") is False
    bank.transfer.assert_not_called()


def test_bank_approve_denies_when_session_moniker_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=7), \
         patch.object(tool.io, "echo"):
        assert tool.bank_approve(args, "") is False
    bank.approve_transfer.assert_not_called()


def test_bank_reject_denies_when_session_moniker_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock()
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "inputinteger", return_value=7), \
         patch.object(tool.io, "echo"):
        assert tool.bank_reject(args, "") is False
    bank.reject_transfer.assert_not_called()


def test_check_access_uses_args_moniker_when_session_moniker_omitted():
    """When subcommand passes no resolved moniker, fall back to
    ``args.moniker`` (the --moniker flag)."""
    tool = _import_tool()
    args = _make_args(moniker="alice", sysop=False)
    assert tool._check_access(args, "balance", moniker="alice") is True
    assert tool._check_access(args, "balance", moniker="bob") is False


def test_check_access_aliases_from_to_from_in_message():
    """``from_`` keyword in the CLI maps to ``from`` in the wire-shaped
    message dict that bbsengine6.bank.access() reads."""
    tool = _import_tool()
    args = _make_args()
    seen = {}

    def spy(args_, op, /, **kwargs):
        seen["session"] = kwargs.get("session")
        seen["message"] = kwargs.get("message")
        return True

    with patch.object(tool, "_bank_access", side_effect=spy):
        # session_moniker=alice, from=alice, to=bob -> own from -> True
        assert tool._check_access(
            args, "transfer", session_moniker="alice",
            from_="alice", to="bob",
        ) is True
    assert seen["message"].get("from") == "alice"
    assert "from_" not in seen["message"]


def test_check_access_returns_false_emits_error_echo():
    """When access is denied the helper prints a one-line error so the
    caller can short-circuit. Pin the echo contract."""
    tool = _import_tool()
    args = _make_args()
    with patch.object(tool.io, "echo") as echo:
        ok = tool._check_access(
            args, "balance", session_moniker="alice", moniker="bob"
        )
    assert ok is False
    assert any("not permitted" in str(c) for c in echo.call_args_list)


def test_make_session_uses_session_moniker_then_args_moniker():
    """``_make_session`` prefers the explicit session_moniker, then
    falls back to ``args.moniker``."""
    tool = _import_tool()
    args = _make_args(moniker="alice")
    s1 = tool._make_session(args, moniker="explicit")
    assert s1.moniker == "explicit"
    s2 = tool._make_session(args)
    assert s2.moniker == "alice"
    s3 = tool._make_session(args, moniker=None)
    assert s3.moniker == "alice"
    s4 = tool._make_session(args, moniker="")
    assert s4.moniker == "alice"


def test_make_session_is_sysop_reflects_args_sysop_flag():
    tool = _import_tool()
    args_a = _make_args(sysop=True)
    args_b = _make_args(sysop=False)
    assert tool._make_session(args_a).is_sysop is True
    assert tool._make_session(args_b).is_sysop is False


def test_make_session_prefers_session_moniker_from_token_claims():
    """``_make_session`` uses the moniker passed to it (the caller
    resolves the actor moniker) and combines ``args._session_is_sysop``
    with the explicit ``--sysop`` flag for ``.is_sysop``."""
    tool = _import_tool()
    args = _make_args(moniker="bob")
    args._session_is_sysop = True
    s = tool._make_session(args, moniker="alice")
    assert s.moniker == "alice"
    assert s.is_sysop is True


def test_make_session_args_sysop_flag_overrides_session_is_sysop():
    """The explicit ``--sysop`` flag still wins over the claim-derived
    is_sysop (the operator outranks the session)."""
    tool = _import_tool()
    args = _make_args(sysop=True)
    args._session_moniker = "alice"
    args._session_is_sysop = False
    s = tool._make_session(args)
    assert s.is_sysop is True


# ---------------------------------------------------------------------
# TokenFileAuth -- --token-file plumbing, _authenticate_ws, and the
# per-call token forwarded through _BedBankFacade to
# BedBankServiceClient.


def _make_args_with_token(
    *,
    token_file: str | None = None,
    moniker: str | None = None,
    direct: bool = False,
    **overrides: Any,
) -> argparse.Namespace:
    args = _make_args(
        token_file=token_file, moniker=moniker, direct=direct, **overrides
    )
    args._backend = "bed" if not direct else "direct"
    return args


def _write_token(path, content: str) -> "os.PathLike":
    """Write a token file in mode 0600 so the perm check passes.

    ``tmp_path`` files default to mode 0644 which trips
    :func:`bed.tools._token.check_token_file_perms`. The bank tool
    itself writes mode 0600 (see :func:`_authenticate_ws`), so the
    test fixture should mirror production perms.
    """
    path.write_text(content)
    path.chmod(0o600)
    return path


def test_buildargs_registers_token_file_flag_as_optional():
    """``--token-file`` registers with default=None."""
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    a = parser.parse_args([])
    assert hasattr(a, "token_file")
    assert a.token_file is None


def test_buildargs_parses_explicit_token_file():
    tool = _import_tool()
    parser = argparse.ArgumentParser()
    tool.buildargs(parser)
    a = parser.parse_args(["--token-file", "/tmp/my.token"])
    assert a.token_file == "/tmp/my.token"


def test_resolve_call_token_reads_explicit_token_file(tmp_path):
    """When ``--token-file`` is set and the file holds a token, the
    call-token helper returns its contents."""
    tool = _import_tool()
    token_path = _write_token(tmp_path / "tok", "secret\n")
    args = _make_args(token_file=str(token_path))
    assert tool._resolve_call_token(args) == "secret"


def test_resolve_call_token_returns_empty_when_token_file_missing(
    tmp_path, monkeypatch
):
    """When neither ``--token-file`` nor the default path has a
    token, the call-token helper returns ``""`` so the client falls
    back to the WS-bound session token."""
    tool = _import_tool()
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    args = _make_args(token_file=str(tmp_path / "missing"))
    assert tool._resolve_call_token(args) == ""


def test_resolve_call_token_caches_value_on_first_call(tmp_path):
    """The first call caches ``args._resolved_token`` so subsequent
    calls do not re-read the file."""
    tool = _import_tool()
    token_path = _write_token(tmp_path / "tok", "first\n")
    args = _make_args(token_file=str(token_path))
    assert tool._resolve_call_token(args) == "first"
    args._resolved_token = "cached"
    assert tool._resolve_call_token(args) == "cached"


def test_authenticate_ws_missing_token_file_renders_hint(tmp_path, monkeypatch):
    """No token file and no default path -> one-line hint pointing
    the operator at ``bed auth login``."""
    tool = _import_tool()
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    args = _make_args(token_file=str(tmp_path / "missing"))
    args._backend = "bed"
    with patch.object(tool.io, "echo") as echo:
        ok = tool._authenticate_ws(args)
    assert ok is False
    msgs = [str(c.args[0]) for c in echo.call_args_list]
    assert any("no bearer token found" in m for m in msgs)
    assert any("bed auth login" in m for m in msgs)


def test_authenticate_ws_present_token_file_calls_reconnect(
    tmp_path, monkeypatch
):
    """A present, valid token file triggers a single
    ``BedAuthServiceClient.reconnect(token=...)`` call."""
    tool = _import_tool()
    from bed.client import authservice as authservice_mod

    token_path = _write_token(tmp_path / "tok", "the-token\n")
    args = _make_args(token_file=str(token_path))
    args._backend = "bed"

    reconnect_mock = AsyncMock(
        return_value={
            "ok": True,
            "moniker": "alice",
            "is_sysop": False,
            "session_id": "s1",
            "token": "the-token",
            "expires_at": "2030-01-01T00:00:00Z",
            "replayed": None,
        }
    )
    fake_client = MagicMock()
    fake_client.reconnect = reconnect_mock
    with patch.object(
        authservice_mod, "BedAuthServiceClient", return_value=fake_client
    ):
        ok = tool._authenticate_ws(args)
    assert ok is True
    reconnect_mock.assert_awaited_once_with("the-token")
    assert args._session_moniker == "alice"
    assert args._session_is_sysop is False
    assert args._resolved_token == "the-token"


def test_authenticate_ws_stashes_session_attrs_from_reconnect_reply(
    tmp_path
):
    """``is_sysop=True`` from the reply populates
    ``args._session_is_sysop`` so ``_make_session`` uses it."""
    tool = _import_tool()
    from bed.client import authservice as authservice_mod

    token_path = _write_token(tmp_path / "tok", "admin-tok\n")
    args = _make_args(token_file=str(token_path))
    args._backend = "bed"

    reconnect_mock = AsyncMock(
        return_value={
            "ok": True,
            "moniker": "admin",
            "is_sysop": True,
            "session_id": "s2",
            "token": "admin-tok",
            "expires_at": "2030-01-01T00:00:00Z",
            "replayed": None,
        }
    )
    fake_client = MagicMock()
    fake_client.reconnect = reconnect_mock
    with patch.object(
        authservice_mod, "BedAuthServiceClient", return_value=fake_client
    ):
        tool._authenticate_ws(args)
    assert args._session_moniker == "admin"
    assert args._session_is_sysop is True


def test_authenticate_ws_writes_rotated_token_back_to_file(tmp_path):
    """If the server returns a new token on reconnect, the bank tool
    writes it back to the file so subsequent runs pick it up."""
    tool = _import_tool()
    from bed.client import authservice as authservice_mod

    token_path = _write_token(tmp_path / "tok", "old-token\n")
    args = _make_args(token_file=str(token_path))
    args._backend = "bed"

    reconnect_mock = AsyncMock(
        return_value={
            "ok": True,
            "moniker": "alice",
            "is_sysop": False,
            "session_id": "s3",
            "token": "new-token",
            "expires_at": "2030-01-01T00:00:00Z",
            "replayed": None,
        }
    )
    fake_client = MagicMock()
    fake_client.reconnect = reconnect_mock
    with patch.object(
        authservice_mod, "BedAuthServiceClient", return_value=fake_client
    ):
        ok = tool._authenticate_ws(args)
    assert ok is True
    assert token_path.read_text().strip() == "new-token"
    assert args._resolved_token == "new-token"


def test_authenticate_ws_no_rotation_leaves_file_intact(tmp_path):
    """If the server returns the same token, the file is not rewritten."""
    tool = _import_tool()
    from bed.client import authservice as authservice_mod

    token_path = _write_token(tmp_path / "tok", "same-token\n")
    args = _make_args(token_file=str(token_path))
    args._backend = "bed"

    mtime_before = token_path.stat().st_mtime_ns
    reconnect_mock = AsyncMock(
        return_value={
            "ok": True,
            "moniker": "alice",
            "is_sysop": False,
            "session_id": "s4",
            "token": "same-token",
            "expires_at": "2030-01-01T00:00:00Z",
            "replayed": None,
        }
    )
    fake_client = MagicMock()
    fake_client.reconnect = reconnect_mock
    with patch.object(
        authservice_mod, "BedAuthServiceClient", return_value=fake_client
    ):
        tool._authenticate_ws(args)
    assert token_path.stat().st_mtime_ns == mtime_before
    assert args._resolved_token == "same-token"


@pytest.mark.parametrize(
    "code,label",
    [
        ("token_revoked", "token_revoked"),
        ("token_expired", "token_expired"),
        ("token_invalid", "token_invalid"),
        ("bed_instance_mismatch", "bed_instance_mismatch"),
        ("not_authenticated", "not_authenticated"),
    ],
)
def test_authenticate_ws_reconnect_soft_failure_renders_hint(
    tmp_path, code, label
):
    """Each auth-failure code prints the standard 'no bearer token'
    hint (which the operator interprets as 're-authenticate') plus
    the server's code in parens."""
    tool = _import_tool()
    from bed.client import authservice as authservice_mod

    token_path = _write_token(tmp_path / "tok", "any-token\n")
    args = _make_args(token_file=str(token_path))
    args._backend = "bed"

    reconnect_mock = AsyncMock(
        return_value={"ok": False, "code": code, "message": f"{label} boom"}
    )
    fake_client = MagicMock()
    fake_client.reconnect = reconnect_mock
    with patch.object(
        authservice_mod, "BedAuthServiceClient", return_value=fake_client
    ), patch.object(tool.io, "echo") as echo:
        ok = tool._authenticate_ws(args)
    assert ok is False
    msgs = [str(c.args[0]) for c in echo.call_args_list]
    assert any("no bearer token found" in m for m in msgs)
    assert any("bed auth login" in m for m in msgs)
    assert any(label in m for m in msgs)


def test_authenticate_ws_reconnect_bad_credentials_passthrough(tmp_path):
    """An unexpected code (e.g. bad_credentials) renders as a plain
    ``code: message`` line -- it is not a 're-authenticate' hint."""
    tool = _import_tool()
    from bed.client import authservice as authservice_mod

    token_path = _write_token(tmp_path / "tok", "any-token\n")
    args = _make_args(token_file=str(token_path))
    args._backend = "bed"

    reconnect_mock = AsyncMock(
        return_value={
            "ok": False, "code": "bad_credentials", "message": "nope"
        }
    )
    fake_client = MagicMock()
    fake_client.reconnect = reconnect_mock
    with patch.object(
        authservice_mod, "BedAuthServiceClient", return_value=fake_client
    ), patch.object(tool.io, "echo") as echo:
        ok = tool._authenticate_ws(args)
    assert ok is False
    msgs = [str(c.args[0]) for c in echo.call_args_list]
    assert any("bad_credentials: nope" in m for m in msgs)
    assert not any("no bearer token found" in m for m in msgs)


def test_main_with_args_bed_mode_calls_authenticate_ws(tmp_path):
    """In bed mode, ``main_with_args`` invokes ``_authenticate_ws``
    before ``_resolve_moniker`` so claim-derived values are
    available to the local ``_check_access``."""
    tool = _import_tool()
    token_path = _write_token(tmp_path / "tok", "present\n")
    args = _make_args(token_file=str(token_path), moniker="alice")
    args.direct = False
    args._backend = "bed"
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(
             tool, "_authenticate_ws", return_value=True
         ) as authn, \
         patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", return_value="Q"), \
         patch.object(tool.bottombar, "setbottombar"), \
         patch.object(tool.bbsengine6_screen, "init"), \
         patch.object(tool.io, "echo"):
        tool.main_with_args(args)
    authn.assert_called_once_with(args)


def test_main_with_args_bed_mode_aborts_when_authenticate_ws_fails(
    tmp_path
):
    """A failed ``_authenticate_ws`` short-circuits ``main_with_args``
    -- ``_resolve_moniker`` and ``menu`` are not called."""
    tool = _import_tool()
    token_path = tmp_path / "missing"
    args = _make_args(token_file=str(token_path), moniker="alice")
    args.direct = False
    args._backend = "bed"
    with patch.object(tool._routing, "select_backend", return_value="bed"), \
         patch.object(tool, "_authenticate_ws", return_value=False), \
         patch.object(tool, "_resolve_moniker") as rm, \
         patch.object(tool.io, "inputchoice") as ic, \
         patch.object(tool.bottombar, "setbottombar"), \
         patch.object(tool.bbsengine6_screen, "init"), \
         patch.object(tool.io, "echo"):
        tool.main_with_args(args)
    rm.assert_not_called()
    ic.assert_not_called()


def test_main_with_args_direct_mode_skips_authenticate_ws():
    """``--direct`` bypasses ``_authenticate_ws`` entirely."""
    tool = _import_tool()
    args = _make_args(direct=True, moniker="alice")
    with patch.object(tool._routing, "select_backend", return_value="direct"), \
         patch.object(
             tool, "_authenticate_ws"
         ) as authn, \
         patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", return_value="Q"), \
         patch.object(tool.bottombar, "setbottombar"), \
         patch.object(tool.bbsengine6_screen, "init"), \
         patch.object(tool.io, "echo"):
        tool.main_with_args(args)
    authn.assert_not_called()


def test_resolve_moniker_uses_session_moniker_from_token_claims():
    """When ``args._session_moniker`` is populated by
    ``_authenticate_ws``, ``_resolve_moniker`` prefers it over the
    local DB lookup."""
    tool = _import_tool()
    args = _make_args()
    args._session_moniker = "alice"
    with patch.object(tool, "database") as db, \
         patch.object(tool, "member") as mm:
        moniker = tool._resolve_moniker(args)
    assert moniker == "alice"
    db.getpool.assert_not_called()
    mm.getcurrentmoniker.assert_not_called()


def test_resolve_moniker_falls_back_to_local_db_when_no_session():
    """Without ``args._session_moniker``, ``_resolve_moniker`` falls
    back to ``member.getcurrentmoniker``."""
    tool = _import_tool()
    args = _make_args()
    pool = MagicMock()
    with patch.object(tool.database, "getpool", return_value=pool), \
         patch.object(
             tool.member, "getcurrentmoniker", return_value="local-user"
         ):
        assert tool._resolve_moniker(args) == "local-user"


def test_resolve_moniker_explicit_moniker_beats_session_moniker():
    """The explicit ``--moniker`` flag wins over the claim-derived
    session moniker (operator override)."""
    tool = _import_tool()
    args = _make_args(moniker="operator-target")
    args._session_moniker = "alice"
    assert tool._resolve_moniker(args) == "operator-target"


def test_resolve_moniker_returns_none_emits_error():
    """If nothing resolves the moniker, ``_resolve_moniker`` prints
    an actionable error and returns None."""
    tool = _import_tool()
    args = _make_args()
    pool = MagicMock()
    with patch.object(tool.database, "getpool", return_value=pool), \
         patch.object(tool.member, "getcurrentmoniker", return_value=None), \
         patch.object(tool.io, "echo") as echo:
        moniker = tool._resolve_moniker(args)
    assert moniker is None
    msgs = [str(c.args[0]) for c in echo.call_args_list]
    assert any("Could not determine current user" in m for m in msgs)


def test_facade_forwards_resolved_token_to_bankservice_client(tmp_path):
    """``_BedBankFacade`` constructs ``BedBankServiceClient`` with
    ``token=`` set to ``_resolve_call_token(args)`` so every wire
    call carries the per-call token."""
    tool = _import_tool()
    from bed.client import singleton as singleton_mod
    from bed.client.bankservice import BedBankServiceClient

    token_path = _write_token(tmp_path / "tok", "wire-tok\n")
    args = _make_args(token_file=str(token_path))
    args._resolved_token = "wire-tok"
    args._backend = "bed"
    fake_conn = MagicMock()
    with patch.object(
        tool, "_resolve_call_token", return_value="wire-tok"
    ), patch.object(
        singleton_mod, "get_bed_connection", return_value=fake_conn
    ):
        facade = tool._BedBankFacade(args)
    assert isinstance(facade._client, BedBankServiceClient)
    assert facade._client._token == "wire-tok"


def test_facade_passes_empty_token_when_unresolved(tmp_path):
    """When ``_resolve_call_token`` returns ``""``, the facade builds
    the client with ``token=""`` so legacy session-bound paths are
    preserved (e.g. direct mode, tests)."""
    tool = _import_tool()
    from bed.client import singleton as singleton_mod

    args = _make_args(token_file=str(tmp_path / "missing"))
    args._backend = "bed"
    with patch.object(tool, "_resolve_call_token", return_value=""), \
         patch.object(
             singleton_mod, "get_bed_connection", return_value=MagicMock()
         ):
        facade = tool._BedBankFacade(args)
    assert facade._client._token == ""


# ---------------------------------------------------------------------
# bottombar fragments
#
# Tests for the bed.bank bottombar wiring: left side "bed.bank (<v>)",
# moniker+balance fragment (with dirty-flag re-query), and host:port /
# direct fragment. The four module-level globals
# ``_current_args / _current_moniker / _current_balance /
# _balance_dirty`` are mutated by menu() and the bank_* callables, so
# every test in this section saves them in a fixture and restores
# them afterwards so test bleed doesn't poison the next run.


@pytest.fixture
def _save_bank_state():
    tool = _import_tool()
    saved = (
        tool._current_args,
        tool._current_moniker,
        tool._current_balance,
        tool._balance_dirty,
    )
    yield tool
    (
        tool._current_args,
        tool._current_moniker,
        tool._current_balance,
        tool._balance_dirty,
    ) = saved


def _set_state(
    tool,
    *,
    args=None,
    moniker="",
    balance=None,
    dirty=True,
):
    """Write the four module-level bottombar state globals in one shot."""
    tool._current_args = args
    tool._current_moniker = moniker
    tool._current_balance = balance
    tool._balance_dirty = dirty


class TestBankBottombarFragments:
    """Behavior of the two bottombar fragments in isolation."""

    def test_moniker_balance_fragment_renders_cached_value(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        _set_state(
            tool,
            args=_make_args(moniker="alice"),
            moniker="alice",
            balance=100,
            dirty=False,
        )
        assert tool._bank_moniker_balance_fragment() == "alice: 100"

    def test_moniker_balance_fragment_renders_unknown_when_balance_none(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        _set_state(
            tool,
            args=_make_args(moniker="alice"),
            moniker="alice",
            balance=None,
            dirty=False,
        )
        assert tool._bank_moniker_balance_fragment() == "alice: ?"

    def test_moniker_balance_fragment_returns_empty_when_no_moniker(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        _set_state(tool, args=_make_args(), moniker="", dirty=False)
        assert tool._bank_moniker_balance_fragment() == ""

    def test_moniker_balance_fragment_refetches_when_dirty(
        self, _save_bank_state
    ):
        """A dirty fragment hits the bank service on the next render."""
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(
            tool, args=args, moniker="alice", balance=42, dirty=True
        )
        bank = _make_bank_mock(get_balance=MagicMock(return_value=150))
        with patch.object(tool, "_bank_service", return_value=bank):
            rendered = tool._bank_moniker_balance_fragment()
        assert rendered == "alice: 150"
        assert tool._current_balance == 150
        assert tool._balance_dirty is False
        bank.get_balance.assert_called_once_with("alice")

    def test_moniker_balance_fragment_swallows_refetch_failure(
        self, _save_bank_state
    ):
        """If the re-query raises, the fragment returns the last known
        balance rather than crashing the render."""
        tool = _save_bank_state
        _set_state(
            tool,
            args=_make_args(moniker="alice"),
            moniker="alice",
            balance=42,
            dirty=True,
        )
        with patch.object(
            tool, "_bank_service",
            side_effect=RuntimeError("db down"),
        ):
            rendered = tool._bank_moniker_balance_fragment()
        assert rendered == "alice: 42"

    def test_moniker_balance_fragment_no_refetch_when_args_unbound(
        self, _save_bank_state
    ):
        """Before menu() entry the args slot is None, so the fragment
        can't hit the DB -- it returns 'alice: ?' without raising."""
        tool = _save_bank_state
        _set_state(
            tool, args=None, moniker="alice", balance=None, dirty=True
        )
        assert tool._bank_moniker_balance_fragment() == "alice: ?"

    def test_host_fragment_bed_mode_shows_host_port(self, _save_bank_state):
        tool = _save_bank_state
        args = _make_args(bed_host="h", bed_port=9999)
        args._backend = "bed"
        _set_state(tool, args=args, moniker="alice", dirty=False)
        assert tool._bank_host_fragment() == "h:9999"

    def test_host_fragment_direct_mode_shows_direct(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        args = _make_args(bed_host="h", bed_port=9999)
        args._backend = "direct"
        _set_state(tool, args=args, moniker="alice", dirty=False)
        assert tool._bank_host_fragment() == "direct"

    def test_host_fragment_uses_defaults_when_attrs_missing(
        self, _save_bank_state
    ):
        """A bare Namespace without bed_host/bed_port/_backend falls
        back to localhost:8765 (the routing-layer defaults)."""
        tool = _save_bank_state
        args = argparse.Namespace()
        _set_state(tool, args=args, moniker="alice", dirty=False)
        assert tool._bank_host_fragment() == "localhost:8765"

    def test_host_fragment_returns_empty_when_args_unbound(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        _set_state(tool, args=None, moniker="alice", dirty=False)
        assert tool._bank_host_fragment() == ""


class TestBankBalanceCacheWiring:
    """Verify each bank_* op touches the cache correctly."""

    def test_bank_balance_caches_value_and_marks_clean(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", dirty=True)
        bank = _make_bank_mock(get_balance=MagicMock(return_value=100))
        with patch.object(tool, "_bank_service", return_value=bank), \
             patch.object(tool.io, "echo"):
            tool.bank_balance(args, "alice")
        assert tool._current_balance == 100
        assert tool._balance_dirty is False

    def test_bank_balance_failure_marks_dirty(self, _save_bank_state):
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", dirty=False)
        bank = _make_bank_mock(get_balance=MagicMock(side_effect=RuntimeError("db")))
        with patch.object(tool, "_bank_service", return_value=bank), \
             patch.object(tool.io, "echo"):
            with pytest.raises(RuntimeError):
                tool.bank_balance(args, "alice")
        assert tool._balance_dirty is True

    def test_bank_balance_access_denied_marks_dirty(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", dirty=False)
        with patch.object(tool, "_check_access", return_value=False), \
             patch.object(tool.io, "echo"):
            ok = tool.bank_balance(args, "alice")
        assert ok is False
        assert tool._balance_dirty is True

    def test_bank_add_success_caches_new_balance(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", dirty=True)
        bank = _make_bank_mock(
            add_funds=MagicMock(
                return_value={
                    "success": True,
                    "new_balance": 150,
                    "message": "added",
                }
            )
        )
        with patch.object(tool.io, "inputinteger", return_value=50), \
             patch.object(tool, "_bank_service", return_value=bank), \
             patch.object(tool.io, "echo"):
            tool.bank_add(args, "alice")
        assert tool._current_balance == 150
        assert tool._balance_dirty is False

    def test_bank_add_failure_marks_dirty(self, _save_bank_state):
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", dirty=False)
        bank = _make_bank_mock(
            add_funds=MagicMock(
                return_value={"success": False, "message": "nope"}
            )
        )
        with patch.object(tool.io, "inputinteger", return_value=50), \
             patch.object(tool, "_bank_service", return_value=bank), \
             patch.object(tool.io, "echo"):
            tool.bank_add(args, "alice")
        assert tool._balance_dirty is True

    def test_bank_add_invalid_amount_marks_dirty(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", dirty=False)
        with patch.object(tool.io, "inputinteger", return_value=0), \
             patch.object(tool.io, "echo"):
            ok = tool.bank_add(args, "alice")
        assert ok is False
        assert tool._balance_dirty is True

    def test_bank_remove_success_caches_new_balance(
        self, _save_bank_state
    ):
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", dirty=True)
        bank = _make_bank_mock(
            remove_funds=MagicMock(
                return_value={
                    "success": True,
                    "new_balance": 75,
                    "message": "removed",
                }
            )
        )
        with patch.object(tool.io, "inputinteger", return_value=25), \
             patch.object(tool, "_bank_service", return_value=bank), \
             patch.object(tool.io, "echo"):
            tool.bank_remove(args, "alice")
        assert tool._current_balance == 75
        assert tool._balance_dirty is False

    def test_bank_transfer_success_marks_dirty(self, _save_bank_state):
        """A transfer between two accounts changes our balance in an
        unknown direction (we could be the source or the recipient),
        so we mark dirty and let the next render re-query."""
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", balance=100, dirty=False)
        bank = _make_bank_mock(
            transfer=MagicMock(
                return_value={"success": True, "message": "ok", "transfer_id": 7}
            )
        )
        with patch.object(tool.io, "inputstring", return_value="bob"), \
             patch.object(tool.io, "inputinteger", return_value=10), \
             patch.object(tool, "_bank_service", return_value=bank), \
             patch.object(tool.io, "echo"):
            tool.bank_transfer(args, "alice")
        assert tool._balance_dirty is True

    def test_bank_approve_success_marks_dirty(self, _save_bank_state):
        tool = _save_bank_state
        args = _make_args(moniker="alice")
        _set_state(tool, args=args, moniker="alice", balance=100, dirty=False)
        bank = _make_bank_mock(
            approve_transfer=MagicMock(
                return_value={"success": True, "message": "ok", "transfer_id": 7}
            )
        )
        with patch.object(tool.io, "inputinteger", return_value=7), \
             patch.object(tool, "_bank_service", return_value=bank), \
             patch.object(tool.io, "echo"):
            tool.bank_approve(args, "alice")
        assert tool._balance_dirty is True


class TestBankMenuBottombarLifecycle:
    """End-to-end wiring through ``menu()``."""

    def test_menu_registers_fragments_on_entry(self):
        tool = _import_tool()
        args = _make_args(moniker="alice")
        saved = (
            tool._current_args,
            tool._current_moniker,
            tool._current_balance,
            tool._balance_dirty,
        )
        try:
            args._backend = "bed"
            with patch.object(tool, "_resolve_moniker", return_value="alice"), \
                 patch.object(tool.io, "inputchoice", return_value="Q"), \
                 patch.object(
                     tool.bottombar, "register_bottombar_fragment"
                 ) as reg, \
                 patch.object(tool.bottombar, "setbottombar"), \
                 patch.object(tool.bbsengine6_screen, "init"), \
                 patch.object(tool.io, "echo"), \
                 patch.object(tool.io, "echo"):
                tool.menu(args, "alice")
            ids = [c.args[0] for c in reg.call_args_list]
            assert tool._bank_host_fragment in ids
            assert tool._bank_moniker_balance_fragment in ids
            assert ids.index(tool._bank_host_fragment) < (
                ids.index(tool._bank_moniker_balance_fragment)
            )
        finally:
            (
                tool._current_args,
                tool._current_moniker,
                tool._current_balance,
                tool._balance_dirty,
            ) = saved

    def test_menu_unregisters_fragments_on_exit(self):
        tool = _import_tool()
        args = _make_args(moniker="alice")
        saved = (
            tool._current_args,
            tool._current_moniker,
            tool._current_balance,
            tool._balance_dirty,
        )
        try:
            args._backend = "bed"
            with patch.object(tool, "_resolve_moniker", return_value="alice"), \
                 patch.object(tool.io, "inputchoice", return_value="Q"), \
                 patch.object(
                     tool.bottombar, "unregister_bottombar_fragment"
                 ) as unreg, \
                 patch.object(tool.bottombar, "setbottombar"), \
                 patch.object(tool.bbsengine6_screen, "init"), \
                 patch.object(tool.io, "echo"), \
                 patch.object(tool.io, "echo"):
                tool.menu(args, "alice")
            ids = [c.args[0] for c in unreg.call_args_list]
            assert tool._bank_moniker_balance_fragment in ids
            assert tool._bank_host_fragment in ids
        finally:
            (
                tool._current_args,
                tool._current_moniker,
                tool._current_balance,
                tool._balance_dirty,
            ) = saved

    def test_menu_unregisters_fragments_on_keyboard_interrupt(self):
        tool = _import_tool()
        args = _make_args(moniker="alice")
        saved = (
            tool._current_args,
            tool._current_moniker,
            tool._current_balance,
            tool._balance_dirty,
        )
        try:
            args._backend = "bed"
            with patch.object(tool, "_resolve_moniker", return_value="alice"), \
                 patch.object(
                     tool.io, "inputchoice",
                     side_effect=KeyboardInterrupt,
                 ), \
                 patch.object(
                     tool.bottombar, "unregister_bottombar_fragment"
                 ) as unreg, \
                 patch.object(tool.bottombar, "setbottombar"), \
                 patch.object(tool.bbsengine6_screen, "init"), \
                 patch.object(tool.io, "echo"), \
                 patch.object(tool.io, "echo"):
                try:
                    tool.menu(args, "alice")
                except KeyboardInterrupt:
                    pass
            ids = [c.args[0] for c in unreg.call_args_list]
            assert tool._bank_moniker_balance_fragment in ids
            assert tool._bank_host_fragment in ids
        finally:
            (
                tool._current_args,
                tool._current_moniker,
                tool._current_balance,
                tool._balance_dirty,
            ) = saved

    def test_menu_unregisters_fragments_on_eof(self):
        tool = _import_tool()
        args = _make_args(moniker="alice")
        saved = (
            tool._current_args,
            tool._current_moniker,
            tool._current_balance,
            tool._balance_dirty,
        )
        try:
            args._backend = "bed"
            with patch.object(tool, "_resolve_moniker", return_value="alice"), \
                 patch.object(tool.io, "inputchoice", side_effect=EOFError), \
                 patch.object(
                     tool.bottombar, "unregister_bottombar_fragment"
                 ) as unreg, \
                 patch.object(tool.bottombar, "setbottombar"), \
                 patch.object(tool.bbsengine6_screen, "init"), \
                 patch.object(tool.io, "echo"), \
                 patch.object(tool.io, "echo"):
                try:
                    tool.menu(args, "alice")
                except EOFError:
                    pass
            ids = [c.args[0] for c in unreg.call_args_list]
            assert tool._bank_moniker_balance_fragment in ids
            assert tool._bank_host_fragment in ids
        finally:
            (
                tool._current_args,
                tool._current_moniker,
                tool._current_balance,
                tool._balance_dirty,
            ) = saved

    def test_menu_left_side_format_is_bed_bank_version(self):
        tool = _import_tool()
        args = _make_args(moniker="alice")
        saved = (
            tool._current_args,
            tool._current_moniker,
            tool._current_balance,
            tool._balance_dirty,
        )
        try:
            args._backend = "bed"
            from bed import _version as bed_version
            with patch.object(tool, "_resolve_moniker", return_value="alice"), \
                 patch.object(tool.io, "inputchoice", return_value="Q"), \
                 patch.object(tool.bottombar, "setbottombar") as sb, \
                 patch.object(tool.bbsengine6_screen, "init"), \
                 patch.object(tool.io, "echo"), \
                 patch.object(tool.io, "echo"):
                tool.menu(args, "alice")
            assert sb.call_args_list
            first_left = sb.call_args_list[0].args[1]
            assert first_left.startswith("bed.bank (")
            assert first_left.endswith(")")
            assert bed_version.__version__ in first_left
        finally:
            (
                tool._current_args,
                tool._current_moniker,
                tool._current_balance,
                tool._balance_dirty,
            ) = saved

    def test_setbottombar_recalled_after_each_subcommand(self):
        """The user wants a redraw after every subcommand so the
        bottom bar reflects the new balance. The first setbottombar
        call is at menu entry; subsequent calls follow each non-Q
        subcommand. The cleanup render after exit goes through
        ``io.echo`` (which erases the bottom row), not
        ``setbottombar``."""
        tool = _import_tool()
        args = _make_args(moniker="alice")
        saved = (
            tool._current_args,
            tool._current_moniker,
            tool._current_balance,
            tool._balance_dirty,
        )
        try:
            args._backend = "bed"
            with patch.object(tool, "_resolve_moniker", return_value="alice"), \
                 patch.object(
                     tool.io, "inputchoice",
                     side_effect=["B", "A", "Q"],
                 ), \
                 patch.object(
                     tool, "bank_balance",
                     return_value=True,
                 ), \
                 patch.object(
                     tool.io, "inputinteger", return_value=10,
                 ), \
                 patch.object(
                     tool, "bank_add", return_value=True,
                 ), \
                 patch.object(tool.bottombar, "setbottombar") as sb, \
                 patch.object(tool.bbsengine6_screen, "init"), \
                 patch.object(tool.io, "echo") as clear_echo:
                tool.menu(args, "alice")
            assert len(sb.call_args_list) == 3
            for c in sb.call_args_list:
                assert c.args[1].startswith("bed.bank (")
            assert clear_echo.called
            clear_arg = clear_echo.call_args.args[0]
            assert "{savecursor}" in clear_arg
            assert "{el}" in clear_arg
            assert "{reset}" in clear_arg
            assert "{restorecursor}" in clear_arg
        finally:
            (
                tool._current_args,
                tool._current_moniker,
                tool._current_balance,
                tool._balance_dirty,
            ) = saved

    def test_menu_calls_screen_init_on_entry(self):
        """``io.screen.init()`` must run before setbottombar so the
        scroll region (top/bottom margins) is in effect when the
        bar is drawn."""
        tool = _import_tool()
        args = _make_args(moniker="alice")
        saved = (
            tool._current_args,
            tool._current_moniker,
            tool._current_balance,
            tool._balance_dirty,
            tool._screen_initialized,
        )
        try:
            args._backend = "bed"
            with patch.object(tool, "_resolve_moniker", return_value="alice"), \
                 patch.object(tool.io, "inputchoice", return_value="Q"), \
                 patch.object(tool.bbsengine6_screen, "init") as init, \
                 patch.object(tool.bottombar, "setbottombar"), \
                 patch.object(tool.io, "echo"), \
                 patch.object(tool.io, "echo"):
                tool.menu(args, "alice")
            init.assert_called_once_with()
        finally:
            (
                tool._current_args,
                tool._current_moniker,
                tool._current_balance,
                tool._balance_dirty,
                tool._screen_initialized,
            ) = saved

    def test_menu_screen_init_only_called_once_across_invocations(self):
        """Calling ``menu()`` twice in the same process must not
        re-init the screen -- ``screen.init()`` writes ANSI escape
        sequences every time it runs, which is wasteful and visible
        on slower terminals."""
        tool = _import_tool()
        args = _make_args(moniker="alice")
        saved = (
            tool._current_args,
            tool._current_moniker,
            tool._current_balance,
            tool._balance_dirty,
            tool._screen_initialized,
        )
        try:
            args._backend = "bed"
            with patch.object(tool, "_resolve_moniker", return_value="alice"), \
                 patch.object(tool.io, "inputchoice", return_value="Q"), \
                 patch.object(tool.bbsengine6_screen, "init") as init, \
                 patch.object(tool.bottombar, "setbottombar"), \
                 patch.object(tool.io, "echo"), \
                 patch.object(tool.io, "echo"):
                tool.menu(args, "alice")
                tool.menu(args, "alice")
            init.assert_called_once_with()
        finally:
            (
                tool._current_args,
                tool._current_moniker,
                tool._current_balance,
                tool._balance_dirty,
                tool._screen_initialized,
            ) = saved

