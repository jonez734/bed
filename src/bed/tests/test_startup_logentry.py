"""bed logs its startup result and every startup failure through bbsengine6's logentry().

The success line reports the number of modules enabled. Each failure site writes one specific line
(stage, exception, the concrete values). Logging must never turn a failed startup into a different crash.
"""

import importlib
import logging
import pathlib
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
MAIN_SRC = (ROOT / "src" / "bed" / "main.py").read_text()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append((record.levelname, record.getMessage()))


@pytest.fixture
def main_mod():
    return importlib.import_module("bed.main")


@pytest.fixture
def captured():
    handler = _Capture()
    logger = logging.getLogger("bbsengine6")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)


@pytest.mark.unit
def test_startup_success_line_is_wired_to_logentry():
    assert "from bbsengine6.util import logentry" in MAIN_SRC
    assert 'f"startup completed, {n_modules} modules enabled"' in MAIN_SRC
    assert '"*default*"' in MAIN_SRC  # the fallback handler is not counted as a module


@pytest.mark.unit
def test_startup_failure_helper_writes_stage_and_details(main_mod, captured):
    main_mod._log_startup_failure(
        "permanent bind failure: OSError errno=98: Address already in use",
        stage="bind",
        binds="127.0.0.1:8765",
    )
    level, msg = captured[-1]
    assert level == "ERROR"
    assert "startup_failed" in msg
    assert "stage=bind" in msg
    assert "binds=127.0.0.1:8765" in msg
    assert "errno=98" in msg


@pytest.mark.unit
def test_startup_failure_helper_never_raises(main_mod, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("log sink down")

    monkeypatch.setattr(main_mod, "logentry", broken)
    main_mod._log_startup_failure("anything", stage="router_load")  # must not raise


@pytest.mark.unit
@pytest.mark.parametrize(
    "binds, expected",
    [
        ([{"host": "127.0.0.1", "port": 8765}], "127.0.0.1:8765"),
        ([{"host": "127.0.0.1", "port": 8765}, {"host": "::1", "port": 8765}], "127.0.0.1:8765,::1:8765"),
        ({"host": "localhost", "port": 9000}, "localhost:9000"),
        (None, "default"),
    ],
)
def test_bind_summary_names_each_address(main_mod, binds, expected):
    args = types.SimpleNamespace(binds=binds)
    assert main_mod._bind_summary(args) == expected


@pytest.mark.unit
def test_each_failure_site_logs_its_stage():
    # Every startup failure exit names its stage, so the log says which step failed.
    for stage in ("database_bootstrap", "router_load", "bind"):
        assert f'stage="{stage}"' in MAIN_SRC, stage
    assert MAIN_SRC.count("_log_startup_failure(") >= 4  # the helper def plus three call sites
