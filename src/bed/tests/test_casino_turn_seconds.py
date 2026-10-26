"""The play clock's ceiling is enforced before a casino table create or update reaches the database.

casino.clock.max_turn_seconds (bed.json) is the sysop ceiling. The casino service refuses a
turn_seconds that is not an integer, is negative, or is above the ceiling, and does not call the
database for a refused request.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bed.api import casino as casino_mod
from bed.api.casino import CasinoService


def _svc(ceiling=120):
    svc = CasinoService.__new__(CasinoService)
    svc.args = SimpleNamespace(_casino_config={"clock": {"max_turn_seconds": ceiling}})
    svc._check_access = MagicMock(return_value=(SimpleNamespace(auth_service_token="tok"), None))
    svc._call = MagicMock(return_value={"type": "table_created", "moniker": "t1", "location": "", "hidden": False, "message": ""})
    return svc


def _send(svc, msg_type, **fields):
    return asyncio.run(svc.handle_message(None, MagicMock(), "/", {"type": msg_type, **fields}))


@pytest.mark.unit
def test_create_at_the_ceiling_is_accepted_and_reaches_the_database():
    svc = _svc(120)
    out = _send(svc, "casino_create_table", game_type="blackjack", moniker="t1", turn_seconds=120)
    assert out["type"] == "table_created"
    svc._call.assert_called_once()


@pytest.mark.unit
def test_create_above_the_ceiling_is_refused_before_the_database():
    svc = _svc(120)
    out = _send(svc, "casino_create_table", game_type="blackjack", moniker="t1", turn_seconds=121)
    assert out["type"] == "error"
    assert out["code"] == "turn_seconds_over_ceiling"
    assert "121" in out["message"] and "120" in out["message"]
    svc._call.assert_not_called()


@pytest.mark.unit
def test_update_above_the_ceiling_is_refused_before_the_database():
    svc = _svc(60)
    out = _send(svc, "casino_update_table", moniker="t1", turn_seconds=90)
    assert out["code"] == "turn_seconds_over_ceiling"
    svc._call.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("bad", [-1, "30", 1.5, True, None])
def test_non_integer_or_negative_is_refused(bad):
    svc = _svc(120)
    out = _send(svc, "casino_update_table", moniker="t1", turn_seconds=bad)
    assert out["type"] == "error"
    assert out["code"] == "invalid_request"
    svc._call.assert_not_called()


@pytest.mark.unit
def test_zero_turns_the_clock_off_and_is_accepted():
    svc = _svc(120)
    _send(svc, "casino_update_table", moniker="t1", turn_seconds=0)
    svc._call.assert_called_once()


@pytest.mark.unit
def test_zero_ceiling_refuses_any_positive_clock():
    svc = _svc(0)
    out = _send(svc, "casino_update_table", moniker="t1", turn_seconds=1)
    assert out["code"] == "turn_seconds_over_ceiling"


@pytest.mark.unit
def test_requests_without_turn_seconds_are_unchanged():
    svc = _svc(120)
    _send(svc, "casino_update_table", moniker="t1", status="closed")
    svc._call.assert_called_once()


@pytest.mark.unit
def test_ceiling_comes_from_the_casino_clock_config():
    # Reads casino.clock.max_turn_seconds through the same accessor the docs describe.
    from casino.config import get_max_turn_seconds
    assert get_max_turn_seconds(SimpleNamespace(_casino_config={"clock": {"max_turn_seconds": 45}})) == 45
    assert get_max_turn_seconds(SimpleNamespace()) == 120
