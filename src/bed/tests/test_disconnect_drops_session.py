"""A disconnect drops the bed session bound to that websocket.

The transport tells the router about a disconnect by the websocket's id. The channel handler
forwards it, and bed's SessionRegistry drops the session bound to it. Before this, the channel
handler called unregister_session on bed's registry, which has no such method, so every disconnect
logged an error and left the session behind.
"""

from bed.api.session import SessionRegistry


def test_drop_by_websocket_removes_the_bound_session():
    reg = SessionRegistry()
    reg.bind("s1", "ws-1", "alice", False)
    assert reg.get_by_session("s1") is not None
    reg.drop_by_websocket("ws-1")
    assert reg.get_by_session("s1") is None
    assert reg.get_by_websocket("ws-1") is None


def test_drop_by_websocket_leaves_other_sessions_alone():
    reg = SessionRegistry()
    reg.bind("s1", "ws-1", "alice", False)
    reg.bind("s2", "ws-2", "bob", False)
    reg.drop_by_websocket("ws-1")
    assert reg.get_by_session("s2") is not None


def test_drop_by_unknown_websocket_is_a_no_op():
    reg = SessionRegistry()
    reg.bind("s1", "ws-1", "alice", False)
    reg.drop_by_websocket("ws-unknown")
    assert reg.get_by_session("s1") is not None


def test_transport_websocket_id_matches_the_bound_session():
    # bed binds the session with str(websocket.id) (api/auth.py). The transport passes the same
    # str(websocket.id) on disconnect, so the session the bind created is the one dropped.
    class _Websocket:
        id = 42

    reg = SessionRegistry()
    reg.bind("s-42", str(_Websocket.id), "carol", False)
    reg.drop_by_websocket(str(_Websocket.id))
    assert reg.get_by_session("s-42") is None
