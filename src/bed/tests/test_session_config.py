"""bed.json's session.idle_minutes is applied to bbsengine6 at startup.

Same pattern as the message section: a missing section or key keeps the
bbsengine6 default (15 minutes); bad values are rejected before startup.
"""

import importlib
from unittest.mock import patch

import pytest

bed_main = importlib.import_module("bed.main")


@pytest.mark.unit
def test_missing_session_section_keeps_default():
    with patch("bbsengine6.session.lib.configure") as cfg:
        bed_main._apply_session_config({})
    cfg.assert_not_called()


@pytest.mark.unit
def test_session_idle_minutes_is_applied():
    with patch("bbsengine6.session.lib.configure") as cfg:
        bed_main._apply_session_config({"session": {"idle_minutes": 30}})
    cfg.assert_called_once_with(idle_minutes=30)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -1, "15", 2.5, True])
def test_bad_idle_minutes_rejected(bad):
    with pytest.raises(ValueError):
        bed_main._apply_session_config({"session": {"idle_minutes": bad}})


@pytest.mark.unit
def test_bed_json_declares_session_idle_minutes_15():
    import json
    from pathlib import Path
    data = json.loads((Path(bed_main.__file__).parent / "data" / "bed.json").read_text())
    assert data["session"]["idle_minutes"] == 15


@pytest.mark.unit
def test_apply_session_config_called_at_startup():
    import inspect
    src = inspect.getsource(bed_main.main_async)
    assert "_apply_session_config(loaded_config)" in src
