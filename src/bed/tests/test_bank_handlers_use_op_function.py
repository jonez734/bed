"""The seven bank operations bed serves go through engine.bed_bank_op, not BankService.

bed holds no table privileges on the bank tables, so a handler that calls BankService as the bed
role is refused by the database. Each handler now calls the op function with the token digest,
the op name and its arguments. The reply shape each handler returns is unchanged.
"""

import inspect
import re

import pytest

from bed.api import bank as bank_mod

OPS = {
    "_handle_add": "add",
    "_handle_remove": "remove",
    "_handle_history": "history",
    "_handle_list_all": "list_all",
    "_handle_pending": "pending",
    "_handle_transfer_request": "transfer_request",
    "_handle_transfer_approve": "transfer_approve",
    "_handle_transfer_reject": "transfer_reject",
}


@pytest.mark.unit
@pytest.mark.parametrize("handler,op", list(OPS.items()))
def test_handler_calls_the_op_function_with_its_op_name(handler, op):
    fn = getattr(bank_mod.BankService, handler, None)
    assert fn is not None, f"{handler} missing"
    src = inspect.getsource(fn)
    assert "_bank_op(" in src, f"{handler} must call the op function"
    assert f'"{op}"' in src, f"{handler} must pass op {op!r}"


@pytest.mark.unit
def test_no_handler_calls_banksservice_directly_for_the_seven_ops():
    for handler in OPS:
        src = inspect.getsource(getattr(bank_mod.BankService, handler))
        assert "_get_bank()" not in src, f"{handler} still calls BankService"


@pytest.mark.unit
def test_bank_op_helper_calls_the_definer_with_the_token_digest():
    src = inspect.getsource(bank_mod.BankService._bank_op)
    assert "engine.bed_bank_op" in src
    assert "token_hash" in src
