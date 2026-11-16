"""A session's seat comes back after a reconnect, from the database, not only from memory.

The seat is kept on the in-memory session. A disconnect drops that session, so a reconnect (a new websocket)
began with no table even though the database still seats the player. The session now takes its table from the
database when it is recreated, so a reconnected player is still seated.
"""

import pytest

from bed.api.session import SessionRegistry


pytestmark = pytest.mark.unit


def test_a_recreated_session_takes_its_seat_from_the_database():
    reg = SessionRegistry(seat_lookup=lambda moniker: "blackjack-jam" if moniker == "jam" else None)
    reg.bind("s1", "ws-1", "jam", False)
    reg.set_table_moniker("s1", "blackjack-jam")
    reg.drop_by_websocket("ws-1")

    state = reg.bind("s2", "ws-2", "jam", False)

    assert state.table_moniker == "blackjack-jam"


def test_a_recreated_session_with_no_database_seat_stays_unseated():
    reg = SessionRegistry(seat_lookup=lambda moniker: None)
    state = reg.bind("s1", "ws-1", "alice", False)
    assert state.table_moniker is None


def test_a_session_without_a_lookup_behaves_as_before():
    reg = SessionRegistry()
    state = reg.bind("s1", "ws-1", "jam", False)
    assert state.table_moniker is None
