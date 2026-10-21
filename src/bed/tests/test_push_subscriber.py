"""bed registers the push subscriber with bbsengine6 at startup.

bbsengine6 holds no bed imports. bed owns the subscriber that probes the
daemon, authenticates the token, and binds the moniker for server pushes.
"""

import asyncio
from unittest.mock import MagicMock, patch

import bbsengine6.startup.message_subscription as ms

from bed import push_subscriber


def test_register_installs_subscriber():
    prev = ms.get_subscriber()
    try:
        push_subscriber.register()
        assert ms.get_subscriber() is push_subscriber.subscribe
    finally:
        ms.set_subscriber(prev)


def test_subscribe_declines_when_bed_unreachable():
    args = MagicMock(debug=False)
    with patch.object(push_subscriber, "probe_bed", return_value=False):
        assert asyncio.run(push_subscriber.subscribe(args, "jam")) is False


def test_subscribe_declines_without_token():
    args = MagicMock(debug=False)
    with patch.object(push_subscriber, "probe_bed", return_value=True), \
         patch.object(push_subscriber, "resolve_token", return_value=""):
        assert asyncio.run(push_subscriber.subscribe(args, "jam")) is False


def test_subscribe_declines_when_bound_moniker_differs():
    args = MagicMock(debug=False)
    auth = MagicMock()

    async def reconnect(token):
        return {"ok": True, "moniker": "someone-else"}

    auth.reconnect = reconnect
    with patch.object(push_subscriber, "probe_bed", return_value=True), \
         patch.object(push_subscriber, "resolve_token", return_value="tok"), \
         patch.object(push_subscriber, "get_bed_connection", return_value=MagicMock()), \
         patch.object(push_subscriber, "BedAuthServiceClient", return_value=auth):
        assert asyncio.run(push_subscriber.subscribe(args, "jam")) is False


def test_startup_registers_subscriber_before_serving():
    import importlib
    import inspect

    bed_main = importlib.import_module("bed.main")
    src = inspect.getsource(bed_main.main_async)
    assert "push_subscriber.register()" in src
    assert src.index("push_subscriber.register()") < src.index("ensure_startup(args)")
