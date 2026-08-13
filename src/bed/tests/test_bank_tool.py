"""Tests for bed.tools.bank (the standalone ``bank`` CLI script).

Covers:
- buildargs: registers --databasename/--moniker/--sysop/--debug
- _resolve_moniker: --moniker short-circuit, getcurrentmoniker happy path,
  getcurrentmoniker returning None, and the pool is passed in
- bank_balance / bank_add / bank_remove / bank_transfer / bank_pending /
  bank_history / bank_approve / bank_reject / bank_list_all
  - each delegates to the right bbsengine6.bank.BankService method
  - each prints results via bbsengine6.io.echo
- bank_add / bank_remove / bank_transfer reject non-positive amounts
- bank_balance, bank_history, bank_list_all, bank_pending tolerate empty data
- main() short-circuits when _resolve_moniker returns None
- main() catches KeyboardInterrupt / EOFError cleanly
"""

from __future__ import annotations

import argparse
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch



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
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_pending(args, "alice", is_sysop=True) is True
    bank.get_pending_transfers.assert_called_once_with("alice", is_sysop=True)
    # at least one echo call rendered a row
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "1" in rendered
    assert "a -> b" in rendered


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
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_history(args, "alice") is True
    rendered = "\n".join(c.args[0] for c in echo.call_args_list)
    assert "7" in rendered
    assert "credit" in rendered
    assert "50" in rendered


# ---------------------------------------------------------------------
# bank_list_all


def test_bank_list_all_empty():
    tool = _import_tool()
    args = _make_args()
    bank = _make_bank_mock(list_all=MagicMock(return_value=[]))
    with patch.object(tool, "_bank_service", return_value=bank), \
         patch.object(tool.io, "echo") as echo:
        assert tool.bank_list_all(args, "alice") is True
    bank.list_all.assert_called_once_with()
    assert any("No accounts" in c.args[0] for c in echo.call_args_list)


def test_bank_list_all_renders_rows():
    tool = _import_tool()
    args = _make_args()
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
         patch.object(tool, "_bank_service") as bs:
        tool.main_with_args(args)
    bs.assert_not_called()


def test_main_keyboard_interrupt_swallowed():
    """Ctrl-C in the menu loop is caught and does not propagate."""
    tool = _import_tool()
    args = _make_args(moniker="alice")
    with patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", side_effect=KeyboardInterrupt), \
         patch.object(tool.io, "echo") as echo:
        tool.main_with_args(args)
    assert any("*INTR*" in c.args[0] for c in echo.call_args_list)


def test_main_eof_swallowed():
    tool = _import_tool()
    args = _make_args(moniker="alice")
    with patch.object(tool, "_resolve_moniker", return_value="alice"), \
         patch.object(tool.io, "inputchoice", side_effect=EOFError), \
         patch.object(tool.io, "echo") as echo:
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
    with patch.object(
        facade, "get_pending_transfers", return_value=rows,
    ) as pending_mock, \
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
    args = _make_args()
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
