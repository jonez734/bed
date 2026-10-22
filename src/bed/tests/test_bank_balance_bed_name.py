"""bank_balance carries the bed's configured name, so the client can label the balance."""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from bed.api import bank as bank_mod
from bed.client.bank import BedBankClient
from test_bank_service import _make_args, _make_session


@pytest.mark.unit
def test_bank_service_accepts_a_bed_name():
    assert "bed_name" in inspect.signature(bank_mod.BankService.__init__).parameters


@pytest.mark.unit
def test_balance_reply_carries_the_bed_name():
    ws, sm = _make_session(moniker="alice")
    service = bank_mod.BankService(_make_args(), sm, bed_name="zoid6")
    cur = MagicMock()
    cur.fetchone.return_value = (True, 250)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    service._bank_conn = MagicMock(return_value=conn)

    async def run():
        return await service._handle_balance(ws, {"type": "bank_balance", "moniker": "alice"})

    r = asyncio.run(run())
    assert r["bed_name"] == "zoid6"
    assert r["balance"] == 250


@pytest.mark.unit
def test_client_returns_the_bed_name_from_the_balance_reply():
    conn = MagicMock()
    c = BedBankClient(conn)
    c._request = AsyncMock(return_value={"balance": 5, "account": True, "bed_name": "zoid6"})
    r = asyncio.run(c.get_balance_result("alice"))
    assert r["bed_name"] == "zoid6"


@pytest.mark.unit
def test_client_reports_no_bed_name_from_an_older_bed():
    conn = MagicMock()
    c = BedBankClient(conn)
    c._request = AsyncMock(return_value={"balance": 5, "account": True})
    r = asyncio.run(c.get_balance_result("alice"))
    assert r["bed_name"] is None
