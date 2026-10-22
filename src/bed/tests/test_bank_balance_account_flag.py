"""bank_balance reports whether the account exists, so 'no account' differs from a zero balance."""

import asyncio
from unittest.mock import MagicMock

from bed.api.bank import BankService

from test_bank_service import _make_args, _make_session


def _conn(account_row, balance):
    """A connection whose cursor returns the definer function's (exists, balance) row."""
    cur = MagicMock()
    exists = account_row is not None
    cur.fetchone.return_value = (exists, balance if exists else 0)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = False
    return conn


def _reply(service, ws, moniker="alice"):
    async def runner():
        return await service._handle_balance(ws, {"type": "bank_balance", "moniker": moniker})
    return asyncio.run(runner())


def test_existing_account_reports_account_true_with_balance():
    ws, sm = _make_session(moniker="alice")
    service = BankService(_make_args(), sm)
    service._bank_conn = MagicMock(return_value=_conn({"moniker": "alice"}, 250))
    r = _reply(service, ws)
    assert r["account"] is True and r["balance"] == 250


def test_missing_account_reports_account_false():
    ws, sm = _make_session(moniker="alice")
    service = BankService(_make_args(), sm)
    service._bank_conn = MagicMock(return_value=_conn(None, 0))
    r = _reply(service, ws)
    assert r["account"] is False


def test_real_zero_balance_reports_account_true():
    ws, sm = _make_session(moniker="alice")
    service = BankService(_make_args(), sm)
    service._bank_conn = MagicMock(return_value=_conn({"moniker": "alice"}, 0))
    r = _reply(service, ws)
    assert r["account"] is True and r["balance"] == 0
