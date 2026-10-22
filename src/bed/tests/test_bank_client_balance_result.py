"""BedBankClient.get_balance_result returns the balance and whether the account exists."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from bed.client.bank import BedBankClient


def _client(reply):
    c = BedBankClient(MagicMock())
    c._request = AsyncMock(return_value=reply)
    return c


def test_result_carries_balance_and_account_flag():
    r = asyncio.run(_client({"balance": 42, "account": True}).get_balance_result("alice"))
    assert r == {"balance": 42, "account": True}


def test_result_for_missing_account_is_account_false():
    r = asyncio.run(_client({"balance": 0, "account": False}).get_balance_result("bob"))
    assert r["account"] is False


def test_get_balance_still_returns_an_int():
    assert asyncio.run(_client({"balance": 7, "account": True}).get_balance("alice")) == 7
