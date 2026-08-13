"""Tests for bed BankService and BedBankServiceClient.

Server side covers the bed.api.bank.BankService handler:
- HANDLED_TYPES registration
- _handle_balance / _handle_add / _handle_remove / _handle_history
- missing-moniker, invalid-amount, db-error envelopes
- lazy ``bbsengine6.bank.BankService`` construction

Client side covers bed.client.bankservice.BedBankServiceClient:
- get_balance / add_funds / remove_funds / get_history envelopes
- singleton helpers (get_bank_client / reset_bank_client)
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------
# Helpers


def _make_args():
    args = MagicMock()
    args.databasename = "test_db"
    args.databasehost = "localhost"
    args.databaseport = 5432
    args.databaseuser = "test_user"
    args.databasepassword = "test_pass"
    return args


def _make_server():
    server = MagicMock()
    server.register_service = MagicMock()
    return server


def _make_session_manager():
    return MagicMock()


def _make_bank_mock(
    *,
    balance: int = 100,
    add_result: Dict[str, Any] | None = None,
    remove_result: Dict[str, Any] | None = None,
    history_rows: List[Dict[str, Any]] | None = None,
    transfer_result: Dict[str, Any] | None = None,
    approve_result: Dict[str, Any] | None = None,
    reject_result: Dict[str, Any] | None = None,
    pending_rows: List[Dict[str, Any]] | None = None,
    list_rows: List[Dict[str, Any]] | None = None,
) -> Any:
    """Build a MagicMock that quacks like bbsengine6.bank.BankService."""
    bank = MagicMock()
    bank.get_balance = MagicMock(return_value=balance)
    bank.add_funds = MagicMock(
        return_value=add_result
        if add_result is not None
        else {"success": True, "new_balance": balance + 50}
    )
    bank.remove_funds = MagicMock(
        return_value=remove_result
        if remove_result is not None
        else {"success": True, "new_balance": balance - 25}
    )
    bank.get_history = MagicMock(return_value=history_rows or [])
    bank.transfer = MagicMock(
        return_value=transfer_result
        if transfer_result is not None
        else {"success": True, "transfer_id": 1, "message": "queued"}
    )
    bank.approve_transfer = MagicMock(
        return_value=approve_result
        if approve_result is not None
        else {
            "success": True,
            "transfer_id": 1,
            "from_balance": 50,
            "to_balance": 80,
        }
    )
    bank.reject_transfer = MagicMock(
        return_value=reject_result
        if reject_result is not None
        else {"success": True, "transfer_id": 1, "message": "rejected"}
    )
    bank.get_pending_transfers = MagicMock(return_value=pending_rows or [])
    bank.list_all = MagicMock(return_value=list_rows or [])
    return bank


# ---------------------------------------------------------------------
# bed.api.bank.BankService — registration


def test_bank_service_registers_handled_types():
    """register_all registers exactly the nine bank_* message types."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    server = _make_server()
    service.register_all(server)

    assert server.register_service.call_count == 1
    call_args = server.register_service.call_args
    assert call_args[0][0] is service
    types = call_args[0][1]
    assert "bank_balance" in types
    assert "bank_add" in types
    assert "bank_remove" in types
    assert "bank_history" in types
    assert "bank_transfer_request" in types
    assert "bank_transfer_approve" in types
    assert "bank_transfer_reject" in types
    assert "bank_pending" in types
    assert "bank_list_all" in types
    assert set(types) == {
        "bank_balance",
        "bank_add",
        "bank_remove",
        "bank_history",
        "bank_transfer_request",
        "bank_transfer_approve",
        "bank_transfer_reject",
        "bank_pending",
        "bank_list_all",
    }


# ---------------------------------------------------------------------
# bed.api.bank.BankService — _handle_balance


def test_handle_balance_happy_path():
    """_handle_balance returns the balance from bbsengine6.bank.BankService."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    service._get_bank = MagicMock(return_value=_make_bank_mock(balance=123))

    async def runner():
        return await service._handle_balance({"type": "bank_balance", "moniker": "alice"})

    result = asyncio.run(runner())
    assert result["type"] == "bank_balance"
    assert result["moniker"] == "alice"
    assert result["balance"] == 123


def test_handle_balance_rejects_missing_moniker():
    """_handle_balance returns missing_moniker envelope on empty moniker."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())

    async def runner():
        return await service._handle_balance({"type": "bank_balance", "moniker": ""})

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "missing_moniker"


def test_handle_balance_db_error_envelope():
    """A bbsengine6 exception is caught and surfaced as database_error."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    fake_bank = MagicMock()
    fake_bank.get_balance.side_effect = RuntimeError("db down")
    service._get_bank = MagicMock(return_value=fake_bank)

    async def runner():
        return await service._handle_balance({"type": "bank_balance", "moniker": "alice"})

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "database_error"
    assert "db down" in result["message"]


# ---------------------------------------------------------------------
# bed.api.bank.BankService — _handle_add


def test_handle_add_happy_path():
    """_handle_add returns new_balance on success."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(
        add_result={"success": True, "new_balance": 250}
    )
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_add(
            {
                "type": "bank_add",
                "moniker": "alice",
                "amount": 50,
                "description": "bonus",
            }
        )

    result = asyncio.run(runner())
    assert result["type"] == "bank_add"
    assert result["moniker"] == "alice"
    assert result["amount"] == 50
    assert result["new_balance"] == 250
    bank.add_funds.assert_called_once_with(
        "alice", 50, transaction_type="credit", description="bonus"
    )


def test_handle_add_rejects_zero_amount():
    """A zero or negative amount yields invalid_amount envelope."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())

    async def runner():
        return await service._handle_add(
            {"type": "bank_add", "moniker": "alice", "amount": 0}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "invalid_amount"


def test_handle_add_rejects_non_integer_amount():
    """A non-integer amount yields invalid_amount envelope."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())

    async def runner():
        return await service._handle_add(
            {"type": "bank_add", "moniker": "alice", "amount": "fifty"}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "invalid_amount"


def test_handle_add_propagates_bbsengine6_failure():
    """A success=False bbsengine6 result is surfaced as a database_error envelope."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(
        add_result={"success": False, "message": "Amount must be positive"}
    )
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_add(
            {"type": "bank_add", "moniker": "alice", "amount": 5}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "database_error"
    assert "Amount must be positive" in result["message"]


def test_handle_add_db_exception_envelope():
    """An exception in add_funds is caught and surfaced."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = MagicMock()
    bank.add_funds.side_effect = RuntimeError("conn lost")
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_add(
            {"type": "bank_add", "moniker": "alice", "amount": 5}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "database_error"
    assert "conn lost" in result["message"]


# ---------------------------------------------------------------------
# bed.api.bank.BankService — _handle_remove


def test_handle_remove_happy_path():
    """_handle_remove returns new_balance on success."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(
        remove_result={"success": True, "new_balance": 75}
    )
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_remove(
            {
                "type": "bank_remove",
                "moniker": "alice",
                "amount": 25,
                "description": "rent",
            }
        )

    result = asyncio.run(runner())
    assert result["type"] == "bank_remove"
    assert result["moniker"] == "alice"
    assert result["amount"] == 25
    assert result["new_balance"] == 75
    bank.remove_funds.assert_called_once_with(
        "alice", 25, transaction_type="debit", description="rent"
    )


def test_handle_remove_insufficient_funds_envelope():
    """An insufficient-funds result is surfaced as a database_error envelope."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(
        remove_result={"success": False, "message": "Insufficient funds. Balance: 5"}
    )
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_remove(
            {"type": "bank_remove", "moniker": "alice", "amount": 1000}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "database_error"
    assert "Insufficient funds" in result["message"]


def test_handle_remove_rejects_zero_amount():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())

    async def runner():
        return await service._handle_remove(
            {"type": "bank_remove", "moniker": "alice", "amount": -10}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "invalid_amount"


# ---------------------------------------------------------------------
# bed.api.bank.BankService — _handle_history


def test_handle_history_happy_path():
    """_handle_history returns the rows from bbsengine6.bank.BankService."""
    from bed.api.bank import BankService

    rows = [
        {"id": 2, "amount": 50, "transactiontype": "credit"},
        {"id": 1, "amount": -10, "transactiontype": "debit"},
    ]
    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(history_rows=rows)
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_history(
            {"type": "bank_history", "moniker": "alice", "limit": 50}
        )

    result = asyncio.run(runner())
    assert result["type"] == "bank_history"
    assert result["moniker"] == "alice"
    assert result["transactions"] == rows
    bank.get_history.assert_called_once_with("alice", 50)


def test_handle_history_default_limit():
    """A missing ``limit`` falls back to 50."""
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock()
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_history(
            {"type": "bank_history", "moniker": "alice"}
        )

    result = asyncio.run(runner())
    assert result["type"] == "bank_history"
    bank.get_history.assert_called_once_with("alice", 50)


def test_handle_history_rejects_negative_limit():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())

    async def runner():
        return await service._handle_history(
            {"type": "bank_history", "moniker": "alice", "limit": -1}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "invalid_amount"


# ---------------------------------------------------------------------
# bed.api.bank.BankService — _handle_transfer_request


def test_handle_transfer_request_happy_path():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(
        transfer_result={"success": True, "transfer_id": 42, "message": "ok"}
    )
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_transfer_request(
            {
                "type": "bank_transfer_request",
                "from": "alice",
                "to": "bob",
                "amount": 25,
                "requested_by": "alice",
            }
        )

    result = asyncio.run(runner())
    assert result["type"] == "bank_transfer_request"
    assert result["transfer_id"] == 42
    assert result["message"] == "ok"
    bank.transfer.assert_called_once_with("alice", "bob", 25, "alice")


def test_handle_transfer_request_rejects_missing_moniker():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())

    async def runner():
        return await service._handle_transfer_request(
            {
                "type": "bank_transfer_request",
                "from": "",
                "to": "bob",
                "amount": 25,
                "requested_by": "alice",
            }
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "missing_moniker"


def test_handle_transfer_request_rejects_non_positive_amount():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())

    async def runner():
        return await service._handle_transfer_request(
            {
                "type": "bank_transfer_request",
                "from": "alice",
                "to": "bob",
                "amount": 0,
                "requested_by": "alice",
            }
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "invalid_amount"


def test_handle_transfer_request_bbsengine6_failure_envelope():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(
        transfer_result={"success": False, "message": "no such account"}
    )
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_transfer_request(
            {
                "type": "bank_transfer_request",
                "from": "alice",
                "to": "bob",
                "amount": 25,
                "requested_by": "alice",
            }
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "operation_failed"
    assert "no such account" in result["message"]


def test_handle_transfer_request_db_exception_envelope():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = MagicMock()
    bank.transfer.side_effect = RuntimeError("db down")
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_transfer_request(
            {
                "type": "bank_transfer_request",
                "from": "alice",
                "to": "bob",
                "amount": 25,
                "requested_by": "alice",
            }
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "database_error"


# ---------------------------------------------------------------------
# bed.api.bank.BankService — _handle_transfer_approve / _handle_transfer_reject


def test_handle_transfer_approve_happy_path():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(
        approve_result={
            "success": True,
            "transfer_id": 5,
            "from_balance": 25,
            "to_balance": 75,
        }
    )
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_transfer_approve(
            {"type": "bank_transfer_approve", "transfer_id": 5, "responded_by": "alice"}
        )

    result = asyncio.run(runner())
    assert result["type"] == "bank_transfer_approve"
    assert result["transfer_id"] == 5
    assert result["from_balance"] == 25
    assert result["to_balance"] == 75
    bank.approve_transfer.assert_called_once_with(5, "alice")


def test_handle_transfer_approve_rejects_zero_id():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())

    async def runner():
        return await service._handle_transfer_approve(
            {"type": "bank_transfer_approve", "transfer_id": 0, "responded_by": "alice"}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "invalid_amount"


def test_handle_transfer_reject_happy_path():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(
        reject_result={"success": True, "transfer_id": 5, "message": "rejected"}
    )
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_transfer_reject(
            {"type": "bank_transfer_reject", "transfer_id": 5, "responded_by": "alice"}
        )

    result = asyncio.run(runner())
    assert result["type"] == "bank_transfer_reject"
    assert result["transfer_id"] == 5
    bank.reject_transfer.assert_called_once_with(5, "alice")


# ---------------------------------------------------------------------
# bed.api.bank.BankService — _handle_pending


def test_handle_pending_happy_path():
    from bed.api.bank import BankService

    rows = [{"id": 1, "from_moniker": "a", "to_moniker": "b", "amount": 10}]
    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(pending_rows=rows)
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_pending(
            {
                "type": "bank_pending",
                "moniker": "alice",
                "is_sysop": True,
            }
        )

    result = asyncio.run(runner())
    assert result["type"] == "bank_pending"
    assert result["transfers"] == rows
    assert result["is_sysop"] is True
    bank.get_pending_transfers.assert_called_once_with("alice", True)


def test_handle_pending_default_non_sysop():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock()
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_pending(
            {"type": "bank_pending", "moniker": "alice"}
        )

    result = asyncio.run(runner())
    assert result["is_sysop"] is False
    bank.get_pending_transfers.assert_called_once_with("alice", False)


def test_handle_pending_db_exception_envelope():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = MagicMock()
    bank.get_pending_transfers.side_effect = RuntimeError("db down")
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_pending(
            {"type": "bank_pending", "moniker": "alice"}
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "database_error"


# ---------------------------------------------------------------------
# bed.api.bank.BankService — _handle_list_all


def test_handle_list_all_happy_path():
    from bed.api.bank import BankService

    rows = [
        {"moniker": "alice", "balance": 100},
        {"moniker": "bob", "balance": 50},
    ]
    service = BankService(_make_args(), _make_session_manager())
    bank = _make_bank_mock(list_rows=rows)
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_list_all({"type": "bank_list_all"})

    result = asyncio.run(runner())
    assert result["type"] == "bank_list_all"
    assert result["accounts"] == rows
    bank.list_all.assert_called_once_with()


def test_handle_list_all_db_exception_envelope():
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    bank = MagicMock()
    bank.list_all.side_effect = RuntimeError("db down")
    service._get_bank = MagicMock(return_value=bank)

    async def runner():
        return await service._handle_list_all({"type": "bank_list_all"})

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "database_error"


# ---------------------------------------------------------------------
# bed.api.bank.BankService — lazy bbsengine6 import


def test_lazy_bank_construction_only_on_first_message():
    """The underlying bbsengine6.bank.BankService is constructed lazily,
    not at __init__, so registering BankService at bed startup is
    cheap even if the DB is briefly unreachable."""
    from bed.api import bank as bank_module
    from bed.api.bank import BankService

    service = BankService(_make_args(), _make_session_manager())
    assert service._bank is None

    fake_bank = MagicMock()
    fake_bank.get_balance = MagicMock(return_value=0)

    with patch.object(bank_module, "_BBSBankService", return_value=fake_bank) as mock_cls:
        result = asyncio.run(
            service._handle_balance(
                {"type": "bank_balance", "moniker": "alice"}
            )
        )
        # Second call must reuse the cached instance.
        asyncio.run(
            service._handle_balance(
                {"type": "bank_balance", "moniker": "alice"}
            )
        )

    mock_cls.assert_called_once_with(service.args)
    assert result["type"] == "bank_balance"
    assert result["balance"] == 0
    # Cached after first call.
    assert service._bank is fake_bank


# ---------------------------------------------------------------------
# bed.client.bankservice.BedBankServiceClient — get_balance


def test_client_get_balance_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={"type": "bank_balance", "balance": 250}
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.get_balance("alice")

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["balance"] == 250
    assert result["moniker"] == "alice"
    conn.send.assert_awaited_once_with(
        {"type": "bank_balance", "moniker": "alice"}
    )


def test_client_get_balance_missing_moniker_returns_ok_false():
    """Empty moniker short-circuits to ok=False without dialing the server."""
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock()
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.get_balance("   ")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "missing_moniker"
    conn.send.assert_not_awaited()


def test_client_get_balance_error_envelope_propagates_code():
    """An error envelope from the server surfaces as ok=False with code."""
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={
            "type": "error",
            "code": "database_error",
            "message": "boom",
        }
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.get_balance("alice")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "database_error"
    assert result["message"] == "boom"


def test_client_get_balance_bed_unavailable_envelope():
    """A transport failure becomes a bed_unavailable envelope (does not raise)."""
    from bed.client.bankservice import BedBankServiceClient
    from bed.client.exceptions import BedUnavailable

    conn = MagicMock()
    conn.send = AsyncMock(side_effect=BedUnavailable("no conn"))
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.get_balance("alice")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "bed_unavailable"
    assert "no conn" in result["message"]


# ---------------------------------------------------------------------
# bed.client.bankservice.BedBankServiceClient — add_funds / remove_funds


def test_client_add_funds_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={
            "type": "bank_add",
            "amount": 50,
            "new_balance": 350,
        }
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.add_funds("alice", 50, description="bonus")

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["amount"] == 50
    assert result["new_balance"] == 350
    conn.send.assert_awaited_once_with(
        {
            "type": "bank_add",
            "moniker": "alice",
            "amount": 50,
            "description": "bonus",
        }
    )


def test_client_add_funds_rejects_invalid_amount_locally():
    """Negative or non-integer amount short-circuits without a server round-trip."""
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock()
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.add_funds("alice", -5)

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "invalid_amount"
    conn.send.assert_not_awaited()


def test_client_remove_funds_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={
            "type": "bank_remove",
            "amount": 25,
            "new_balance": 75,
        }
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.remove_funds("alice", 25)

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["amount"] == 25
    assert result["new_balance"] == 75
    conn.send.assert_awaited_once_with(
        {
            "type": "bank_remove",
            "moniker": "alice",
            "amount": 25,
            "description": "debit",
        }
    )


def test_client_remove_funds_insufficient_funds_envelope():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={
            "type": "error",
            "code": "database_error",
            "message": "Insufficient funds. Balance: 5",
        }
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.remove_funds("alice", 1000)

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "database_error"
    assert "Insufficient funds" in result["message"]


# ---------------------------------------------------------------------
# bed.client.bankservice.BedBankServiceClient — get_history


def test_client_get_history_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    rows = [{"id": 1, "amount": 50}]
    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={"type": "bank_history", "transactions": rows}
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.get_history("alice", limit=10)

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["transactions"] == rows
    conn.send.assert_awaited_once_with(
        {"type": "bank_history", "moniker": "alice", "limit": 10}
    )


def test_client_get_history_missing_moniker_returns_empty_transactions():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock()
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.get_history("")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "missing_moniker"
    assert result["transactions"] == []
    conn.send.assert_not_awaited()


# ---------------------------------------------------------------------
# bed.client.bankservice.BedBankServiceClient — transfer / approve / reject
# / pending / list_all (new in v1)


def test_client_transfer_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={
            "type": "bank_transfer_request",
            "transfer_id": 7,
            "message": "queued",
        }
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.transfer("alice", "bob", 25, "alice")

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["transfer_id"] == 7
    assert result["message"] == "queued"
    assert result["from_moniker"] == "alice"
    assert result["to_moniker"] == "bob"
    assert result["amount"] == 25
    conn.send.assert_awaited_once_with(
        {
            "type": "bank_transfer_request",
            "from": "alice",
            "to": "bob",
            "amount": 25,
            "requested_by": "alice",
        }
    )


def test_client_transfer_rejects_missing_moniker():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock()
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.transfer("", "bob", 25, "alice")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "missing_moniker"
    conn.send.assert_not_awaited()


def test_client_transfer_rejects_non_positive_amount():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock()
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.transfer("alice", "bob", 0, "alice")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "invalid_amount"
    conn.send.assert_not_awaited()


def test_client_transfer_error_envelope_propagates():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={
            "type": "error",
            "code": "operation_failed",
            "message": "nope",
        }
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.transfer("alice", "bob", 25, "alice")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "operation_failed"
    assert result["message"] == "nope"


def test_client_approve_transfer_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={
            "type": "bank_transfer_approve",
            "transfer_id": 7,
            "from_balance": 50,
            "to_balance": 80,
        }
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.approve_transfer(7, "alice")

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["transfer_id"] == 7
    assert result["from_balance"] == 50
    assert result["to_balance"] == 80
    conn.send.assert_awaited_once_with(
        {
            "type": "bank_transfer_approve",
            "transfer_id": 7,
            "responded_by": "alice",
        }
    )


def test_client_approve_transfer_rejects_zero_id():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock()
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.approve_transfer(0, "alice")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "invalid_amount"
    conn.send.assert_not_awaited()


def test_client_reject_transfer_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={"type": "bank_transfer_reject", "transfer_id": 9}
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.reject_transfer(9, "alice")

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["transfer_id"] == 9
    conn.send.assert_awaited_once_with(
        {
            "type": "bank_transfer_reject",
            "transfer_id": 9,
            "responded_by": "alice",
        }
    )


def test_client_get_pending_transfers_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    rows = [{"id": 1, "from_moniker": "a", "to_moniker": "b", "amount": 10}]
    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={"type": "bank_pending", "transfers": rows}
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.get_pending_transfers("alice", is_sysop=True)

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["transfers"] == rows
    assert result["is_sysop"] is True
    conn.send.assert_awaited_once_with(
        {
            "type": "bank_pending",
            "moniker": "alice",
            "is_sysop": True,
        }
    )


def test_client_get_pending_transfers_bed_unavailable_returns_empty():
    from bed.client.bankservice import BedBankServiceClient
    from bed.client.exceptions import BedUnavailable

    conn = MagicMock()
    conn.send = AsyncMock(side_effect=BedUnavailable("down"))
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.get_pending_transfers("alice")

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "bed_unavailable"
    assert result["transfers"] == []


def test_client_list_all_happy_path():
    from bed.client.bankservice import BedBankServiceClient

    rows = [
        {"moniker": "alice", "balance": 100},
        {"moniker": "bob", "balance": 50},
    ]
    conn = MagicMock()
    conn.send = AsyncMock(
        return_value={"type": "bank_list_all", "accounts": rows}
    )
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.list_all()

    result = asyncio.run(runner())
    assert result["ok"] is True
    assert result["accounts"] == rows
    conn.send.assert_awaited_once_with({"type": "bank_list_all"})


def test_client_list_all_bed_unavailable_returns_empty():
    from bed.client.bankservice import BedBankServiceClient
    from bed.client.exceptions import BedUnavailable

    conn = MagicMock()
    conn.send = AsyncMock(side_effect=BedUnavailable("down"))
    client = BedBankServiceClient(conn)

    async def runner():
        return await client.list_all()

    result = asyncio.run(runner())
    assert result["ok"] is False
    assert result["code"] == "bed_unavailable"
    assert result["accounts"] == []


# ---------------------------------------------------------------------
# bed.client.bankservice — singleton helpers


def test_get_bank_client_returns_same_instance_for_same_connection():
    import bed.client.bankservice as bs_module

    bs_module.reset_bank_client()
    conn = MagicMock()
    a = bs_module.get_bank_client(conn)
    b = bs_module.get_bank_client(conn)
    assert a is b
    bs_module.reset_bank_client()


def test_get_bank_client_rebuilds_when_connection_changes():
    import bed.client.bankservice as bs_module

    bs_module.reset_bank_client()
    conn1 = MagicMock()
    conn2 = MagicMock()
    a = bs_module.get_bank_client(conn1)
    b = bs_module.get_bank_client(conn2)
    assert a is not b
    assert a._conn is conn1
    assert b._conn is conn2
    bs_module.reset_bank_client()


def test_reset_bank_client_drops_cached_instance():
    import bed.client.bankservice as bs_module

    bs_module.reset_bank_client()
    conn = MagicMock()
    bs_module.get_bank_client(conn)
    assert bs_module._module_client is not None

    bs_module.reset_bank_client()
    assert bs_module._module_client is None


def test_bank_client_reexported_from_bed_client():
    """BedBankServiceClient and helpers are part of the public API."""
    import bed.client as client_pkg

    assert "BedBankServiceClient" in client_pkg.__all__
    assert "get_bank_client" in client_pkg.__all__
    assert "reset_bank_client" in client_pkg.__all__
    assert "BedBankServiceClient" in client_pkg.__dict__
    assert "get_bank_client" in client_pkg.__dict__
    assert "reset_bank_client" in client_pkg.__dict__


def test_bank_service_reexported_from_bed_api():
    """BankService is part of bed.api's public API."""
    import bed.api as api_pkg

    assert "BankService" in api_pkg.__all__
    assert "BankService" in api_pkg.__dict__
