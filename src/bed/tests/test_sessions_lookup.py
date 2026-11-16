"""SessionRegistry finds the live sessions a moniker is bound to, and the sessions seated at a table.

A program fans a message out to a player's own socket by session id, which needs "which sessions is this
player connected on" (sessions_for_moniker) and "who is seated at this table" (sessions_at_table). Both are
generic: any program that binds sessions and tracks a table can use them.
"""

from bed.api.session import SessionRegistry


def test_sessions_for_moniker_lists_every_session_bound_to_it():
    reg = SessionRegistry()
    reg.bind("s1", "ws-1", "alice", False)
    reg.bind("s2", "ws-2", "alice", False)
    reg.bind("s3", "ws-3", "bob", False)
    assert sorted(reg.sessions_for_moniker("alice")) == ["ws-1", "ws-2"]


def test_sessions_for_moniker_is_empty_when_none_is_bound():
    reg = SessionRegistry()
    reg.bind("s1", "ws-1", "alice", False)
    assert reg.sessions_for_moniker("nobody") == []


def test_a_dropped_session_is_no_longer_listed():
    reg = SessionRegistry()
    reg.bind("s1", "ws-1", "alice", False)
    reg.drop_by_websocket("ws-1")
    assert reg.sessions_for_moniker("alice") == []


def test_sessions_at_table_lists_the_sessions_seated_there():
    reg = SessionRegistry()
    reg.bind("s1", "ws-1", "alice", False)
    reg.bind("s2", "ws-2", "bob", False)
    reg.bind("s3", "ws-3", "carol", False)
    reg.set_table_moniker("s1", "blackjack-a")
    reg.set_table_moniker("s2", "blackjack-a")
    reg.set_table_moniker("s3", "blackjack-b")
    assert sorted(s.session_id for s in reg.sessions_at_table("blackjack-a")) == ["s1", "s2"]


def test_sessions_at_table_is_empty_for_an_idle_table():
    reg = SessionRegistry()
    reg.bind("s1", "ws-1", "alice", False)
    assert reg.sessions_at_table("blackjack-a") == []


def test_lookups_return_the_connection_ids_the_transport_routes_on():
    # The transport routes a message by the connection's websocket id, not by the bed session id, so the
    # lookups must return the websocket ids (the UUID of each live connection).
    reg = SessionRegistry()
    reg.bind("sess-1", "conn-1", "alice", False)
    reg.bind("sess-2", "conn-2", "alice", False)
    reg.add_spectator("sess-2", "blackjack-a")
    assert sorted(reg.sessions_for_moniker("alice")) == ["conn-1", "conn-2"]
    assert reg.get_table_observers("blackjack-a") == {"conn-2"}
