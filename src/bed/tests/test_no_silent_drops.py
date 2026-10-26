"""Every message type a bed service claims is answered, never silently dropped.

A service that claims a type (HANDLED_TYPES) but cannot route it must reply with an explicit
error, not return None. A None from a service makes the transport send nothing, so the client
waits for a reply that never comes. client_exit was dropped this way before its map entry existed.
"""

import asyncio

import pytest

from bed.api import auth as auth_mod
from bed.api import message as message_mod
from bed.api.auth import AuthService
from bed.api.message import MessageService


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.unit
def test_auth_claims_only_types_it_can_route():
    assert set(AuthService.HANDLED_TYPES) == set(auth_mod._TYPE_TO_OP)


@pytest.mark.unit
def test_message_claims_only_types_it_can_route():
    assert set(MessageService.HANDLED_TYPES) == set(message_mod._TYPE_TO_OP)


@pytest.mark.unit
def test_claimed_type_with_no_route_gets_an_explicit_error(monkeypatch):
    svc = AuthService.__new__(AuthService)
    svc.args = None
    # Simulate the bug: the service claims a type its map no longer routes.
    monkeypatch.setattr(AuthService, "HANDLED_TYPES", ("auth", "ghost_type"))
    out = _run(svc.handle_message(None, None, "/", {"type": "ghost_type"}))
    assert out is not None, "a claimed type must not be dropped with no reply"
    assert out["type"] == "error"
    assert out["code"] == "unhandled_type"
    assert "ghost_type" in out["message"]


@pytest.mark.unit
def test_unclaimed_type_still_returns_none_for_other_services():
    # A type this service does not claim is not its business: None lets the router try the next.
    svc = AuthService.__new__(AuthService)
    svc.args = None
    out = _run(svc.handle_message(None, None, "/", {"type": "bank_balance"}))
    assert out is None


@pytest.mark.unit
def test_registration_refuses_a_service_that_claims_an_unroutable_type():
    from bed.api import auth as a

    class _Server:
        def __init__(self):
            self.registered = []

        def register_service(self, service, types):
            self.registered.append(types)

    class _Broken(AuthService):
        HANDLED_TYPES = ("auth", "no_route_here")

    svc = _Broken.__new__(_Broken)
    svc.args = None
    with pytest.raises(RuntimeError, match="no_route_here"):
        svc.register_all(_Server())
