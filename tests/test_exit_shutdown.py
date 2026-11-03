"""auth and bank call bottombar.shutdown() on every exit, including when the tool raises."""

from unittest.mock import patch

import pytest

from bed.tools import auth, bank


@pytest.mark.unit
@pytest.mark.parametrize("mod", [auth, bank])
def test_exit_calls_shutdown_on_normal_return(mod):
    with patch.object(mod, "_main_with_args_run", return_value=None), \
         patch("bbsengine6.bottombar.shutdown") as shutdown:
        mod.main_with_args(object())
    shutdown.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("mod", [auth, bank])
def test_exit_calls_shutdown_even_when_the_tool_raises(mod):
    with patch.object(mod, "_main_with_args_run", side_effect=RuntimeError("boom")), \
         patch("bbsengine6.bottombar.shutdown") as shutdown:
        with pytest.raises(RuntimeError):
            mod.main_with_args(object())
    shutdown.assert_called_once()
