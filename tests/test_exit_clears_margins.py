"""auth and bank clear the scroll margins on every exit, including when the tool raises."""

from unittest.mock import patch

import pytest

from bed.tools import auth, bank


@pytest.mark.unit
@pytest.mark.parametrize("mod", [auth, bank])
def test_main_with_args_clears_on_normal_return(mod):
    with patch.object(mod, "_main_with_args_run", return_value=None), \
         patch("bbsengine6.bottombar._clear_margins", create=True) as clear:
        mod.main_with_args(object())
    clear.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("mod", [auth, bank])
def test_main_with_args_clears_even_when_the_tool_raises(mod):
    with patch.object(mod, "_main_with_args_run", side_effect=RuntimeError("boom")), \
         patch("bbsengine6.bottombar._clear_margins", create=True) as clear:
        with pytest.raises(RuntimeError):
            mod.main_with_args(object())
    clear.assert_called_once()
