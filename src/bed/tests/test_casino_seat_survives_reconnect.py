"""The casino adapter gives the shared session registry a seat lookup, so a reconnected player keeps their seat.

The registry recreates a session on reconnect with no table. The casino adapter installs a lookup that reads the
database seat, so the recreated session is seated again without the player re-joining.
"""

import argparse
from unittest.mock import patch

import pytest

from bed.api.session import SessionRegistry
from bed.api.casino import CasinoService


pytestmark = pytest.mark.unit


def test_register_all_gives_the_registry_a_seat_lookup():
    reg = SessionRegistry()
    svc = CasinoService(argparse.Namespace(), reg)

    class _Server:
        def register_service(self, *a, **k):
            return None

    with patch("bed.api.casino.check_claims_routable", lambda *a, **k: None):
        svc.register_all(_Server())

    with patch("casino.dal.table.seated_table", return_value="blackjack-jam"):
        state = reg.bind("s9", "ws-9", "jam", False)
    assert state.table_moniker == "blackjack-jam"
