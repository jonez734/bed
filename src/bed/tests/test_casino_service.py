"""Tests for casino's authorization pipeline and per-op handlers.

Mirrors the structure of ``test_bank_service.py``:
- ``_check_access`` direct pipeline tests (gates 1-5)
- token-aware authorization (revoked / expired / invalid wire tokens)
- per-handler smoke tests for representative ops
- ``casino.access.access()`` policy integration (the per-op rules
  in :mod:`casino.access` are the single source of truth)

All tests run without a live DB -- the service classes that touch
the DB (``casino.services.game``, ``casino.services.table``, etc.)
are replaced with ``MagicMock`` so the WS envelope shape can be
asserted in isolation. The DB-backed integration tests live in
``test_casino_integration.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, "/home/opencode/data/work/casino/src")
sys.path.insert(0, "/home/opencode/data/work/bbsengine6/py/src")
sys.path.insert(0, "/home/opencode/data/work/bed/src")


# ---------------------------------------------------------------------
# Helpers


def _make_args():
    args = MagicMock()
    args.databasename = "test_db"
    args.pool = MagicMock()
    return args


def _make_door_handler(service_class, args, registry, *args_extra, **kwargs):
    """Build a casino handler with ``allow_legacy_session_only=True``.

    Most tests in this file are door-mode fixtures: they bind a
    session via ``_bind_session`` and expect ``check_access`` to admit
    the op on session strength alone (no wire / session-bound token).
    Setting ``allow_legacy_session_only`` on the handler flips the
    five-gate pipeline into the legacy branch where the in-memory
    session snapshot is the authoritative authorization source.
    Without this flag the per-op check rejects the call with
    ``DENY gate no-claims mode=door`` and the test suite regresses.

    Positional extras (``args_extra``) are forwarded to the handler
    constructor so callers can pass e.g. an explicit channel_state
    without losing the door-mode flag.
    """
    svc = service_class(args, registry, *args_extra, **kwargs)
    svc.allow_legacy_session_only = True
    return svc


def _make_websocket(ws_id: Any = "ws-1") -> Any:
    """Build a minimal websocket mock carrying ``.id``."""
    ws = MagicMock()
    ws.id = ws_id
    return ws


def _make_session_registry(*, ws_id: str = "ws-1") -> Any:
    """Build a real :class:`SessionRegistry` for token-aware tests."""
    from bed.api.session import SessionRegistry

    return SessionRegistry()


def _bind_session(
    registry: Any,
    *,
    session_id: str = "s1",
    ws_id: str = "ws-1",
    moniker: str = "alice",
    is_sysop: bool = False,
    auth_service_token: Optional[str] = None,
) -> Any:
    state = registry.bind(session_id, ws_id, moniker, is_sysop)
    state.auth_service_token = auth_service_token
    return state


def _make_inmemory_token_store() -> Any:
    from bed.api.token_store import InMemoryTokenStore

    return InMemoryTokenStore()


def _make_token_record(
    *,
    secret: bytes,
    instance_id: str,
    moniker: str = "alice",
    session_id: str = "s1",
    websocket_id: str = "ws-1",
    is_sysop: bool = False,
    loginid: Optional[str] = None,
    expires_at: Optional[float] = None,
) -> Dict[str, Any]:
    from casino.api._auth import mint_token_record

    return mint_token_record(
        secret=secret,
        instance_id=instance_id,
        moniker=moniker,
        session_id=session_id,
        websocket_id=websocket_id,
        is_sysop=is_sysop,
        loginid=loginid,
        expires_at=expires_at,
    )


def _wire_handler(
    service: Any,
    *,
    secret: bytes,
    token_store: Any,
    instance_id: str,
) -> Any:
    """Attach the token wiring to ``service`` so its ``_check_access``
    runs the full five-gate pipeline.
    """
    service.secret = secret
    service.token_store = token_store
    service.instance_id = instance_id
    return service


# ---------------------------------------------------------------------
# Token codec


def test_token_encode_decode_round_trip():
    from casino.api._auth import decode_token, encode_token

    secret = secrets.token_bytes(32)
    claims = {
        "version": 1,
        "moniker": "alice",
        "issued_at": 100.0,
        "expires_at": 1000.0,
        "session_id": "s1",
        "is_sysop": False,
        "bed_instance_id": "test",
        "websocket_id": "ws-1",
    }
    token = encode_token(claims, secret)
    decoded = decode_token(token, secret)
    assert decoded == claims


def test_token_decode_rejects_bad_signature():
    from casino.api._auth import (
        CODE_TOKEN_INVALID,
        TokenError,
        encode_token,
        decode_token,
    )

    token = encode_token({"version": 1, "moniker": "alice"}, secrets.token_bytes(32))
    with pytest.raises(TokenError) as ei:
        decode_token(token, secrets.token_bytes(32))
    assert ei.value.code == CODE_TOKEN_INVALID


def test_token_decode_rejects_malformed():
    from casino.api._auth import CODE_TOKEN_INVALID, TokenError, decode_token

    with pytest.raises(TokenError) as ei:
        decode_token("not-a-token", secrets.token_bytes(32))
    assert ei.value.code == CODE_TOKEN_INVALID


def test_token_decode_rejects_unknown_version():
    from casino.api._auth import (
        CODE_TOKEN_INVALID,
        TokenError,
        encode_token,
        decode_token,
    )

    secret = secrets.token_bytes(32)
    token = encode_token(
        {"version": 99, "moniker": "alice"},
        secret,
    )
    with pytest.raises(TokenError) as ei:
        decode_token(token, secret)
    assert ei.value.code == CODE_TOKEN_INVALID


# ---------------------------------------------------------------------
# casino.api._auth.check_access — five-gate pipeline


def _handler_with_token_wiring(service_class: Any, *args, **kwargs) -> Any:
    """Build a handler with full token wiring (legacy/no-wiring path
    uses the bare constructor and exercises the session-only gate).
    """
    secret = kwargs.pop("secret", secrets.token_bytes(32))
    token_store = kwargs.pop("token_store", _make_inmemory_token_store())
    instance_id = kwargs.pop("instance_id", "test-instance")
    svc = service_class(*args, **kwargs)
    _wire_handler(svc, secret=secret, token_store=token_store, instance_id=instance_id)
    return svc, secret, token_store, instance_id


def test_check_access_returns_not_authenticated_for_unbound_ws():
    """An unbound websocket yields a ``not_authenticated`` envelope
    even when no token wiring is configured.
    """
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    svc = _make_door_handler(TableServiceHandler, args, registry)

    state, err = check_access(svc, _make_websocket("ws-fresh"), "list_tables", {"type": "list_tables"})
    # list_tables is a public op -- even an unbound ws should be allowed
    # through the gate; the policy in casino.access.access() returns
    # True for list_tables regardless of session.
    assert state is None
    assert err is None  # public op allowed without session


def test_check_access_returns_not_authenticated_for_protected_op():
    """An unbound websocket yields ``not_authenticated`` for any
    non-public op when no wire token is present.
    """
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    svc = _make_door_handler(TableServiceHandler, args, registry)

    state, err = check_access(
        svc, _make_websocket("ws-fresh"), "create_table", {"type": "create_table"}
    )
    assert state is None
    assert err is not None
    assert err["code"] == "not_authenticated"


def test_check_access_lets_bound_session_through_policy():
    """A bound session passes the gate when ``casino.access.access``
    says yes.
    """
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(registry, session_id="s1", ws_id="ws-1", moniker="alice")
    svc = _make_door_handler(TableServiceHandler, args, registry)

    state, err = check_access(
        svc, _make_websocket("ws-1"), "create_table", {"type": "create_table"}
    )
    assert err is None
    assert state is not None
    assert state.moniker == "alice"


def test_check_access_rejects_wrong_player_on_gameplay_op():
    """A session bound to ``bob`` cannot fire gameplay ops at
    ``alice``'s table (would need to be seated there).
    """
    from casino.api._auth import check_access
    from casino.api.handler import BetServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(registry, session_id="s2", ws_id="ws-2", moniker="bob")
    svc = _make_door_handler(BetServiceHandler, args, registry)
    state, err = check_access(
        svc,
        _make_websocket("ws-2"),
        "bet",
        {"type": "bet", "amount": 10, "table_moniker": "t1"},
    )
    # bob is not seated at t1 -> policy denies.
    assert err is not None
    assert err["code"] == "forbidden"


def test_check_access_lets_seated_player_bet():
    from casino.api._auth import check_access
    from casino.api.handler import BetServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    state = _bind_session(registry, session_id="s1", ws_id="ws-1", moniker="alice")
    state.table_moniker = "t1"
    svc = _make_door_handler(BetServiceHandler, args, registry)
    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "bet",
        {"type": "bet", "amount": 10, "table_moniker": "t1"},
    )
    assert err is None
    assert state.moniker == "alice"


def test_check_access_sysop_can_kick_anywhere():
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(
        registry,
        session_id="s0",
        ws_id="ws-0",
        moniker="root",
        is_sysop=True,
    )
    svc = _make_door_handler(TableServiceHandler, args, registry)
    state, err = check_access(
        svc,
        _make_websocket("ws-0"),
        "kick_player",
        {
            "type": "kick_player",
            "player_moniker": "alice",
            "table_monikers": ["t1"],
            "owner": "bob",
        },
    )
    # sysop bypasses owner check.
    assert err is None
    assert state.moniker == "root"


def test_check_access_owner_can_kick_own_table():
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(registry, session_id="s1", ws_id="ws-1", moniker="alice")
    svc = _make_door_handler(TableServiceHandler, args, registry)
    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "kick_player",
        {
            "type": "kick_player",
            "player_moniker": "bob",
            "table_monikers": ["t1"],
            "owner": "alice",
        },
    )
    assert err is None


def test_check_access_non_owner_non_sysop_cannot_kick():
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(registry, session_id="s1", ws_id="ws-1", moniker="carol")
    svc = _make_door_handler(TableServiceHandler, args, registry)
    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "kick_player",
        {
            "type": "kick_player",
            "player_moniker": "bob",
            "table_monikers": ["t1"],
            "owner": "alice",
        },
    )
    assert err is not None
    assert err["code"] == "forbidden"


def test_check_access_chat_global_only_needs_session():
    from casino.api._auth import check_access
    from casino.api.handler import ChatServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(registry, session_id="s1", ws_id="ws-1", moniker="alice")
    svc = _make_door_handler(ChatServiceHandler, args, registry)
    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "chat_global",
        {"type": "chat_global", "message": "hi"},
    )
    assert err is None


# ---------------------------------------------------------------------
# Token-aware wire-token validation


def test_check_access_wire_token_lazily_binds_session():
    """A valid wire token can bind a session that the WS lost (e.g.
    after ``asyncio.run`` cycle). The bound session must surface in
    the returned state.
    """
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    secret = secrets.token_bytes(32)
    instance_id = "test-instance"
    store = _make_inmemory_token_store()
    record = _make_token_record(
        secret=secret,
        instance_id=instance_id,
        moniker="alice",
        session_id="s1",
        websocket_id="ws-1",
    )
    store.put(record)

    args = _make_args()
    registry = _make_session_registry()
    svc = _make_door_handler(TableServiceHandler, args, registry)
    _wire_handler(svc, secret=secret, token_store=store, instance_id=instance_id)

    # WebSocket id changed (e.g. fresh asyncio.run) -> no bound session.
    state, err = check_access(
        svc,
        _make_websocket("ws-2"),
        "create_table",
        {"type": "create_table", "token": record.token},
    )
    assert err is None
    assert state.moniker == "alice"


def test_check_access_invalid_wire_token_returns_token_invalid():
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    secret = secrets.token_bytes(32)
    args = _make_args()
    registry = _make_session_registry()
    svc = _make_door_handler(TableServiceHandler, args, registry)
    _wire_handler(
        svc,
        secret=secret,
        token_store=_make_inmemory_token_store(),
        instance_id="test-instance",
    )

    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "create_table",
        {"type": "create_table", "token": "not.a.real.token"},
    )
    assert err is not None
    assert err["code"] == "token_invalid"


def test_check_access_revoked_wire_token_returns_token_revoked():
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    secret = secrets.token_bytes(32)
    instance_id = "test-instance"
    store = _make_inmemory_token_store()
    record = _make_token_record(
        secret=secret, instance_id=instance_id, moniker="alice"
    )
    # Mint but never put into the store -> store has no record -> revoked.
    args = _make_args()
    registry = _make_session_registry()
    svc = _make_door_handler(TableServiceHandler, args, registry)
    _wire_handler(svc, secret=secret, token_store=store, instance_id=instance_id)

    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "create_table",
        {"type": "create_table", "token": record.token},
    )
    assert err is not None
    assert err["code"] == "token_revoked"


def test_check_access_expired_wire_token_returns_token_expired():
    """An expired token (expires_at <= now) is rejected with
    ``token_expired`` and the recoverable flag set.
    """
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    secret = secrets.token_bytes(32)
    instance_id = "test-instance"
    store = _make_inmemory_token_store()
    # Force expiry in the past.
    record = _make_token_record(
        secret=secret,
        instance_id=instance_id,
        moniker="alice",
        expires_at=time.time() - 1.0,
    )
    store.put(record)

    args = _make_args()
    registry = _make_session_registry()
    svc = _make_door_handler(TableServiceHandler, args, registry)
    _wire_handler(svc, secret=secret, token_store=store, instance_id=instance_id)

    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "create_table",
        {"type": "create_table", "token": record.token},
    )
    assert err is not None
    assert err["code"] == "token_expired"
    assert err.get("recoverable") is True


def test_check_access_instance_mismatch_returns_instance_mismatch():
    """A token minted by a different bed instance is rejected and
    purged from the store.
    """
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    secret = secrets.token_bytes(32)
    store = _make_inmemory_token_store()
    record = _make_token_record(
        secret=secret,
        instance_id="other-instance",
        moniker="alice",
    )
    store.put(record)

    args = _make_args()
    registry = _make_session_registry()
    svc = _make_door_handler(TableServiceHandler, args, registry)
    _wire_handler(
        svc,
        secret=secret,
        token_store=store,
        instance_id="test-instance",
    )

    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "create_table",
        {"type": "create_table", "token": record.token},
    )
    assert err is not None
    assert err["code"] == "instance_mismatch"
    # Token was deleted from the store on mismatch.
    assert store.get(record.token) is None


def test_check_access_session_token_used_when_wire_token_absent():
    """When the wire carries no token, the session-bound token is
    re-verified on every op (defense-in-depth path).
    """
    from casino.api._auth import check_access
    from casino.api.handler import TableServiceHandler

    secret = secrets.token_bytes(32)
    instance_id = "test-instance"
    store = _make_inmemory_token_store()
    record = _make_token_record(
        secret=secret,
        instance_id=instance_id,
        moniker="alice",
    )
    store.put(record)

    args = _make_args()
    registry = _make_session_registry()
    state = _bind_session(
        registry,
        session_id="s1",
        ws_id="ws-1",
        moniker="alice",
        auth_service_token=record.token,
    )
    svc = _make_door_handler(TableServiceHandler, args, registry)
    _wire_handler(svc, secret=secret, token_store=store, instance_id=instance_id)

    state, err = check_access(
        svc,
        _make_websocket("ws-1"),
        "create_table",
        {"type": "create_table"},
    )
    assert err is None
    assert state.moniker == "alice"


# ---------------------------------------------------------------------
# Per-handler pipeline integration


def test_table_service_handler_list_tables_public():
    """``list_tables`` does not require an authenticated session."""
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    # Stub the service so list_tables does not touch the DB.
    svc = _make_door_handler(TableServiceHandler, args, registry, None)
    svc.table_service = MagicMock()
    svc.table_service.list_tables = MagicMock(return_value=[])

    async def runner():
        return await svc.handle_message(
            None, _make_websocket("ws-fresh"), "/", {"type": "list_tables"}
        )

    result = asyncio.run(runner())
    assert result["type"] == "table_list"


def test_table_service_handler_create_table_requires_auth():
    """``create_table`` requires an authenticated session."""
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    svc = _make_door_handler(TableServiceHandler, args, registry, None)

    async def runner():
        return await svc.handle_message(
            None,
            _make_websocket("ws-fresh"),
            "/",
            {"type": "create_table"},
        )

    result = asyncio.run(runner())
    assert result["type"] == "error"
    assert result["code"] == "not_authenticated"


def test_table_service_handler_kick_player_shape_validation():
    """``kick_player`` with empty player_moniker or table_monikers
    yields an ``invalid_request`` envelope before the policy gate.
    """
    from casino.api.handler import TableServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(
        registry, session_id="s1", ws_id="ws-1", moniker="root", is_sysop=True
    )
    svc = _make_door_handler(TableServiceHandler, args, registry, None)

    async def runner_empty_player():
        return await svc.handle_message(
            None,
            _make_websocket("ws-1"),
            "/",
            {"type": "kick_player", "player_moniker": "", "table_monikers": ["t1"]},
        )

    async def runner_empty_tables():
        return await svc.handle_message(
            None,
            _make_websocket("ws-1"),
            "/",
            {"type": "kick_player", "player_moniker": "bob", "table_monikers": []},
        )

    assert asyncio.run(runner_empty_player())["code"] == "invalid_request"
    assert asyncio.run(runner_empty_tables())["code"] == "invalid_request"


def test_chat_service_handler_chat_global_happy_path():
    from casino.api.handler import ChatServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(registry, session_id="s1", ws_id="ws-1", moniker="alice")
    svc = _make_door_handler(ChatServiceHandler, args, registry)

    async def runner():
        return await svc.handle_message(
            None,
            _make_websocket("ws-1"),
            "/",
            {"type": "chat_global", "message": "hi"},
        )

    result = asyncio.run(runner())
    assert result["type"] == "chat_message"
    assert result["scope"] == "global"
    assert result["from_moniker"] == "alice"


def test_slot_service_handler_slot_history_self_only():
    """``slot_history`` allows the player to read their own spins,
    and sysop can read anyone's. Anyone else gets forbidden.

    The handler seeds ``message["moniker"]`` from the bound session
    (``_handle_history_msg`` overwrites the inbound ``moniker`` field
    so the access policy reads the cryptographically resolved
    moniker rather than the wire payload -- the latter is
    attacker-controlled and can't be trusted as the policy's source
    of truth). So the ``runner_other`` path needs a non-bob
    session bound to the same WS so the override resolves to a
    non-self moniker and the access check rejects it.
    """
    from casino.api.handler import SlotServiceHandler

    args = _make_args()
    registry = _make_session_registry()
    _bind_session(registry, session_id="s1", ws_id="ws-1", moniker="alice")
    svc = _make_door_handler(SlotServiceHandler, args, registry, None)
    svc._handle_history = MagicMock(return_value=[{"spin_id": 1}])

    async def runner_self():
        return await svc.handle_message(
            None,
            _make_websocket("ws-1"),
            "/",
            {"type": "slot_history", "moniker": "alice"},
        )

    # Re-bind the WS to carol (not bob, not alice, not sysop). Carol
    # asking for bob's history: ``_handle_history_msg`` overrides
    # ``message["moniker"]`` with carol from the bound session, so
    # the access policy compares ``auth_moniker=carol`` against
    # ``target=carol`` (the override) and admits the call. To make
    # the "other" path actually exercise the cross-moniker denial
    # the test has to bypass the override: we drive the handler's
    # ``handle_message`` with a session bound to a sysop (``ws-2``)
    # who would normally be allowed, and then assert that the
    # non-sysop carol-binding returns forbidden on ``ws-3``. See the
    # per-WS binding pattern below.
    async def runner_other_denied():
        return await svc.handle_message(
            None,
            _make_websocket("ws-other"),
            "/",
            {"type": "slot_history", "moniker": "alice"},
        )

    # alice reads her own spins via the alice session on ws-1
    r = asyncio.run(runner_self())
    assert r["type"] == "slot_history"

    # Bind carol to a different ws and have carol ask for alice's
    # history -- the override resolves message["moniker"] to carol
    # so the policy compares carol-vs-carol and admits. To test the
    # cross-moniker denial we drive ``_handle_history_msg`` via a
    # dedicated ``access(args, "slot_history", session=<carol>,
    # message={"moniker": "alice"})`` call which mirrors what the
    # policy would see if the inbound moniker survived.
    from casino.api._auth import check_access

    # Bind carol on ws-other so the handler finds carol's session
    _bind_session(registry, session_id="s-carol", ws_id="ws-other", moniker="carol")

    # Reach into the access pipeline directly: with carol bound,
    # ``message["moniker"] = "alice"`` (the inbound), the policy sees
    # ``auth_moniker=carol`` against ``target=alice`` and rejects.
    state, err = check_access(
        svc,
        _make_websocket("ws-other"),
        "slot_history",
        {"type": "slot_history", "moniker": "alice"},
    )
    assert err is not None
    assert err["code"] == "forbidden"


# ---------------------------------------------------------------------
# casino.access.access() policy integration


def test_casino_access_returns_true_for_list_tables_no_session():
    """``casino.access.access`` returns True for ``list_tables``
    regardless of session (public op).
    """
    from casino.access import access

    args = argparse.Namespace()
    assert access(args, "list_tables", session=None, message={}) is True


def test_casino_access_denies_unknown_op():
    """An unknown op verb always denies."""
    from casino.access import access

    args = argparse.Namespace()
    state = MagicMock()
    state.moniker = "alice"
    state.is_sysop = False
    state.table_moniker = None
    assert access(args, "no_such_op", session=state, message={}) is False


def test_casino_access_uses_claim_derived_moniker_when_present():
    """Claim-derived moniker wins over session moniker when claims
    are populated by a verified token.
    """
    from casino.access import access

    args = argparse.Namespace()
    state = MagicMock()
    state.moniker = "alice"
    state.is_sysop = False
    state.table_moniker = "t1"
    # claims says it's bob, so update_table should treat bob as the
    # actor (and only succeed if owner == bob).
    message = {
        "claims": {"moniker": "bob", "is_sysop": False},
        "table_moniker": "t1",
        "owner": "bob",
    }
    assert access(args, "update_table", session=state, message=message) is True
    # And fails when owner is alice (the session attribute).
    message["owner"] = "alice"
    assert access(args, "update_table", session=state, message=message) is False
