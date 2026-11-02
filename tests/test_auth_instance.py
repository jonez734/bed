"""auth --instance: refuse unless the answering bed reports the named bed_name; the bottombar init runs once."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bed.tools import auth


def _args(instance=None):
    return SimpleNamespace(instance=instance)


@pytest.mark.unit
def test_matching_bed_name_passes():
    with patch.object(auth.io, "echo"):
        assert auth._check_instance(_args("zoid6"), {"ok": True, "bed_name": "zoid6"}) is True


@pytest.mark.unit
def test_mismatched_bed_name_refuses_and_shows_both_values():
    with patch.object(auth.io, "echo") as echo:
        assert auth._check_instance(_args("zoid6"), {"ok": True, "bed_name": "bed"}) is False
    text = " ".join(str(c.args[0]) for c in echo.call_args_list)
    assert "zoid6" in text and "bed" in text


@pytest.mark.unit
def test_instance_requested_but_no_bed_name_is_an_error():
    with patch.object(auth.io, "echo") as echo:
        assert auth._check_instance(_args("zoid6"), {"ok": True}) is False
    assert any("no bed_name" in str(c.args[0]) for c in echo.call_args_list)


@pytest.mark.unit
def test_without_instance_login_proceeds_and_reports_the_bed():
    with patch.object(auth.io, "echo") as echo:
        assert auth._check_instance(_args(None), {"ok": True, "bed_name": "zoid6"}) is True
    assert any("reached bed_name=zoid6" in str(c.args[0]) for c in echo.call_args_list)


@pytest.mark.unit
def test_refusal_stops_before_any_token_is_persisted():
    reply = {"ok": True, "bed_name": "bed", "token": "t"}
    with patch.object(auth, "_persist_token") as persist, \
         patch.object(auth, "_collect_credentials", return_value=("jam", "pw")), \
         patch.object(auth, "_auth_service") as svc, \
         patch.object(auth, "run_with_connection", return_value=reply), \
         patch.object(auth, "_ensure_token_file_arg"), \
         patch.object(auth, "_reject_malformed_token_response", return_value=False), \
         patch.object(auth.io, "echo"):
        svc.return_value = SimpleNamespace(_conn=None, login=lambda m, p: reply)
        assert auth.auth_login(SimpleNamespace(instance="zoid6", subcommand="login")) is False
    persist.assert_not_called()


@pytest.mark.unit
def test_bottombar_init_runs_once_per_process():
    auth._bottombar_initialized = False
    with patch.object(auth.bottombar, "init") as init:
        auth._ensure_bottombar_initialized()
        auth._ensure_bottombar_initialized()
    init.assert_called_once()
    auth._bottombar_initialized = False
