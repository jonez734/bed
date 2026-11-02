"""set_caller: every service receives the bound session's identity as _caller, and a client cannot forge it."""

from types import SimpleNamespace

import pytest

from bed.main import set_caller


def _state(moniker, is_sysop=False):
    return SimpleNamespace(moniker=moniker, is_sysop=is_sysop)


@pytest.mark.unit
def test_bound_session_sets_moniker_and_sysop():
    msg = {"type": "jargon_create"}
    set_caller(msg, _state("alice", is_sysop=False))
    assert msg["_caller"] == {"moniker": "alice", "is_sysop": False}


@pytest.mark.unit
def test_sysop_session_is_marked():
    msg = {"type": "jargon_create"}
    set_caller(msg, _state("root", is_sysop=True))
    assert msg["_caller"] == {"moniker": "root", "is_sysop": True}


@pytest.mark.unit
def test_client_supplied_caller_is_replaced_or_removed():
    """A forged _caller in the body must not survive, bound session or not."""
    forged = {"_caller": {"moniker": "root", "is_sysop": True}}
    set_caller(forged, _state("alice"))
    assert forged["_caller"] == {"moniker": "alice", "is_sysop": False}

    forged = {"_caller": {"moniker": "root", "is_sysop": True}}
    set_caller(forged, None)
    assert forged["_caller"] == {}


@pytest.mark.unit
def test_unbound_socket_gets_an_empty_caller():
    msg = {"type": "jargon_create"}
    set_caller(msg, None)
    assert msg["_caller"] == {}


@pytest.mark.unit
def test_no_module_names_in_the_generic_hook():
    """bed must not name individual programs; the identity rule is generic."""
    from pathlib import Path
    src = Path(__file__).parents[1] / "src" / "bed" / "main.py"
    text = src.read_text()
    start = text.index("def set_caller(")
    body = text[start:text.index("def role_for_session(")]
    for name in ("jargon", "casino", "empyre", "zoid6"):
        assert name not in body.lower()


@pytest.mark.unit
def test_require_registration_defaults_to_true_and_is_refused_when_not_bool(monkeypatch):
    """bed requires registration by default; a non-boolean value is refused, not coerced."""
    import importlib
    from bbsengine6 import module
    bm = importlib.import_module("bed.main")
    seen = []
    monkeypatch.setattr(module, "set_require_registration", lambda v: seen.append(v))
    bm._apply_registration_config({})
    bm._apply_registration_config({"require_registration": False})
    assert seen == [True, False]
    with pytest.raises(ValueError):
        bm._apply_registration_config({"require_registration": "yes"})
