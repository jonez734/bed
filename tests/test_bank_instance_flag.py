"""bank takes --instance as a global option and stops before its menu when the bed name differs."""

import argparse
from unittest.mock import patch

import pytest

from bed.tools import _routing, bank


@pytest.mark.unit
def test_instance_is_a_shared_client_option_for_bank():
    p = argparse.ArgumentParser()
    _routing.build_client_args(p)
    assert p.parse_args(["--instance", "zoid6"]).instance == "zoid6"


@pytest.mark.unit
def test_bank_stops_when_the_name_check_fails():
    args = argparse.Namespace(instance="wrong")
    with patch.object(bank._routing, "select_backend", return_value="bed"), \
         patch("bed.tools.auth._check_instance_name", return_value=False), \
         patch.object(bank, "_authenticate_ws") as auth_ws, \
         patch.object(bank, "_resolve_moniker") as resolve:
        bank.main_with_args(args)
    auth_ws.assert_not_called()
    resolve.assert_not_called()
