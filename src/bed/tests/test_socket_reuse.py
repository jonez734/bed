"""A live websocket is reused across calls; a dead one is replaced.

Regression: _is_closed compared str(ws.state). With the installed websockets, str() of the State
enum is "1", not "State.OPEN", so every open socket was treated as closed and redialed on each
call. Five calls cost five dials instead of one.
"""

from types import SimpleNamespace

from websockets.protocol import State

from bed.client.connection import BedConnection


def _conn():
    return BedConnection.__new__(BedConnection)


def _ws(state):
    # The attributes _is_closed reads. No 'closed' attribute, so the state path is used.
    return SimpleNamespace(state=state)


def test_open_socket_is_not_closed():
    c = _conn()
    c._ws = _ws(State.OPEN)
    assert c._is_closed() is False


def test_connecting_socket_is_not_closed():
    c = _conn()
    c._ws = _ws(State.CONNECTING)
    assert c._is_closed() is False


def test_closing_or_closed_socket_is_closed():
    for state in (State.CLOSING, State.CLOSED):
        c = _conn()
        c._ws = _ws(state)
        assert c._is_closed() is True, state


def test_no_socket_is_closed():
    c = _conn()
    c._ws = None
    assert c._is_closed() is True
