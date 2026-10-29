"""The startup-log file sink: off by default, startup lines only, size rotation, flag override, validation.

The sink is configured by bed.json's startup_log section (path, max_bytes, backups). Only the startup
lines (startup completed / startup_failed) reach the file; the journal line is unchanged.
"""

import importlib
import logging
import types

import pytest

from bbsengine6.util import LOGGER_NAME


@pytest.fixture
def main_mod():
    return importlib.import_module("bed.main")


@pytest.fixture(autouse=True)
def _reset_sink(main_mod):
    main_mod._startup_file_handler = None
    yield
    main_mod._close_startup_log()


def _args(**kw):
    base = dict(startup_log=None, startup_log_path="", startup_log_max_bytes=1048576, startup_log_backups=5)
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.mark.unit
def test_sink_is_off_when_path_is_empty(main_mod, tmp_path):
    main_mod._open_startup_log(_args(startup_log_path=""))
    assert main_mod._startup_file_handler is None


@pytest.mark.unit
def test_startup_line_reaches_the_file(main_mod, tmp_path):
    target = tmp_path / "startup.log"
    main_mod._open_startup_log(_args(startup_log_path=str(target)))
    main_mod._write_startup_line("startup completed, 3 modules enabled", module="bed", action="startup")
    main_mod._close_startup_log()
    text = target.read_text()
    assert "startup completed, 3 modules enabled" in text


@pytest.mark.unit
def test_non_startup_traffic_does_not_reach_the_file(main_mod, tmp_path):
    target = tmp_path / "startup.log"
    main_mod._open_startup_log(_args(startup_log_path=str(target)))
    logging.getLogger(LOGGER_NAME).info("ordinary request traffic")
    main_mod._write_startup_line("startup completed, 3 modules enabled", module="bed", action="startup")
    main_mod._close_startup_log()
    text = target.read_text()
    assert "ordinary request traffic" not in text
    assert "startup completed" in text


@pytest.mark.unit
def test_handler_is_detached_after_each_startup_line(main_mod, tmp_path):
    target = tmp_path / "startup.log"
    main_mod._open_startup_log(_args(startup_log_path=str(target)))
    logger = logging.getLogger(LOGGER_NAME)
    before = list(logger.handlers)
    main_mod._write_startup_line("startup completed, 1 modules enabled", module="bed", action="startup")
    assert list(logger.handlers) == before


@pytest.mark.unit
def test_rotation_happens_at_max_bytes(main_mod, tmp_path):
    target = tmp_path / "startup.log"
    main_mod._open_startup_log(_args(startup_log_path=str(target), startup_log_max_bytes=200, startup_log_backups=2))
    for i in range(40):
        main_mod._write_startup_line(f"startup completed, {i} modules enabled", module="bed", action="startup")
    main_mod._close_startup_log()
    assert (tmp_path / "startup.log.1").exists()
    assert not (tmp_path / "startup.log.3").exists()  # backups capped at 2


@pytest.mark.unit
def test_flag_overrides_bed_json_path(main_mod, tmp_path):
    cfg = {"startup_log": {"path": str(tmp_path / "from-json.log"), "max_bytes": 1000, "backups": 1}}
    args = _args(startup_log=str(tmp_path / "from-flag.log"))
    main_mod._apply_startup_log_config(args, cfg)
    assert args.startup_log_path == str(tmp_path / "from-flag.log")


@pytest.mark.unit
def test_bed_json_path_used_without_flag(main_mod, tmp_path):
    cfg = {"startup_log": {"path": str(tmp_path / "from-json.log"), "max_bytes": 1000, "backups": 1}}
    args = _args()
    main_mod._apply_startup_log_config(args, cfg)
    assert args.startup_log_path == str(tmp_path / "from-json.log")
    assert args.startup_log_max_bytes == 1000 and args.startup_log_backups == 1


@pytest.mark.unit
@pytest.mark.parametrize("bad, key", [
    ({"max_bytes": 0}, "max_bytes"),
    ({"max_bytes": "big"}, "max_bytes"),
    ({"backups": -1}, "backups"),
    ({"backups": True}, "backups"),
    ({"path": 5}, "path"),
])
def test_bad_values_fail_at_startup_naming_the_key(main_mod, bad, key):
    with pytest.raises(ValueError, match=key):
        main_mod._apply_startup_log_config(_args(), {"startup_log": bad})


@pytest.mark.unit
def test_section_must_be_an_object(main_mod):
    with pytest.raises(ValueError, match="startup_log must be an object"):
        main_mod._apply_startup_log_config(_args(), {"startup_log": "/var/log/x"})
