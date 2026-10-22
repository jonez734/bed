"""bank_balance reports whether the account exists, so 'no account' differs from a zero balance."""

import asyncio
from unittest.mock import MagicMock

from bed.api.bank import BankService

from test_bank_service import _make_args, _make_session


def _bank(account_row, balance):
    bank = MagicMock()
    bank.account.get.return_value = account_row
    bank.get_balance.return_value = balance
    return bank


def _reply(service, ws, moniker="alice"):
    async def runner():
        return await service._handle_balance(ws, {"type": "bank_balance", "moniker": moniker})
    return asyncio.run(runner())


def test_existing_account_reports_account_true_with_balance():
    ws, sm = _make_session(moniker="alice")
    service = BankService(_make_args(), sm)
    service._get_bank = MagicMock(return_value=_bank({"moniker": "alice"}, 250))
    r = _reply(service, ws)
    assert r["account"] is True and r["balance"] == 250


def test_missing_account_reports_account_false():
    ws, sm = _make_session(moniker="alice")
    service = BankService(_make_args(), sm)
    service._get_bank = MagicMock(return_value=_bank(None, 0))
    r = _reply(service, ws)
    assert r["account"] is False


def test_real_zero_balance_reports_account_true():
    ws, sm = _make_session(moniker="alice")
    service = BankService(_make_args(), sm)
    service._get_bank = MagicMock(return_value=_bank({"moniker": "alice"}, 0))
    r = _reply(service, ws)
    assert r["account"] is True and r["balance"] == 0
