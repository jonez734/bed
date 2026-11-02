"""auth --instance is a global option: the bed's ping name is checked before any subcommand runs."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bed.tools import auth


class _FakeConn:
    """Mirrors BedConnection: async context manager, and an awaitable send()."""

    def __init__(self, name):
        self._reply = {"type": "pong", "name": name} if name is not None else {"type": "pong"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, message):
        return self._reply


def _run_check(instance, name):
    args = SimpleNamespace(instance=instance)
    with patch("bed.client.get_bed_connection", return_value=_FakeConn(name)), \
         patch.object(auth, "io") as io_mock:
        ok = auth._check_instance_name(args)
    return ok, io_mock


@pytest.mark.unit
def test_matching_name_passes():
    ok, _ = _run_check("zoid6", "zoid6")
    assert ok is True


@pytest.mark.unit
def test_mismatched_name_refuses_and_shows_both():
    ok, io_mock = _run_check("wrong", "zoid6")
    assert ok is False
    text = " ".join(str(c.args[0]) for c in io_mock.echo.call_args_list)
    assert "wrong" in text and "zoid6" in text and "no token written" in text


@pytest.mark.unit
def test_bed_without_a_name_is_refused_when_instance_is_given():
    ok, io_mock = _run_check("zoid6", None)
    assert ok is False
    assert any("sent no name" in str(c.args[0]) for c in io_mock.echo.call_args_list)


@pytest.mark.unit
def test_no_instance_reports_the_bed_and_proceeds():
    ok, io_mock = _run_check(None, "zoid6")
    assert ok is True
    assert any("reached bed zoid6" in str(c.args[0]) for c in io_mock.echo.call_args_list)


@pytest.mark.unit
def test_instance_is_a_shared_client_option():
    import argparse
    from bed.tools import _routing
    p = argparse.ArgumentParser()
    _routing.build_client_args(p)
    assert p.parse_args(["--instance", "zoid6"]).instance == "zoid6"


@pytest.mark.unit
def test_bottombar_init_runs_once_per_process():
    auth._bottombar_initialized = False
    with patch.object(auth.bottombar, "init") as init:
        auth._ensure_bottombar_initialized()
        auth._ensure_bottombar_initialized()
    init.assert_called_once()
    auth._bottombar_initialized = False
