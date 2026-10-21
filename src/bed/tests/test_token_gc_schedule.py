"""Token cleanup runs every 300 seconds and does not block the event loop.

The loop starts after the server binds and is cancelled by stop(). Each tick
calls gc_expired in a worker thread, so a slow database call cannot stall
WebSocket dispatch.
"""

import asyncio
import importlib
import inspect
from unittest.mock import MagicMock

bed_main = importlib.import_module("bed.main")


def test_gc_interval_is_300_seconds():
    assert bed_main.TOKEN_GC_INTERVAL_SECONDS == 300


def test_gc_loop_sleeps_for_configured_interval():
    src = inspect.getsource(bed_main.BED._gc_loop)
    assert "TOKEN_GC_INTERVAL_SECONDS" in src
    assert "sleep(60)" not in src


def test_gc_loop_runs_gc_in_worker_thread():
    src = inspect.getsource(bed_main.BED._gc_loop)
    assert "asyncio.to_thread" in src


def test_gc_loop_calls_gc_expired_each_tick():
    async def run():
        bed = bed_main.BED.__new__(bed_main.BED)
        bed._running = True
        store = MagicMock()
        bed.token_store = store
        calls = {"n": 0}

        async def fake_sleep(_):
            calls["n"] += 1
            bed._running = False

        orig = asyncio.sleep
        bed_main.asyncio.sleep = fake_sleep
        try:
            await bed._gc_loop()
        finally:
            bed_main.asyncio.sleep = orig
        return store.gc_expired.call_count

    assert asyncio.run(run()) == 1


def test_gc_task_started_after_bind_and_cancelled_on_stop():
    start_src = inspect.getsource(bed_main.BED.start)
    stop_src = inspect.getsource(bed_main.BED.stop)
    assert start_src.index("self.server.start()") < start_src.index("self._gc_task = asyncio.create_task")
    assert "self._gc_task.cancel()" in stop_src
