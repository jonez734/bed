# bed/api/gate.py
# The token gates every zoidoffice-facing message family runs before it does any work.
#
# Session resolution, the wire-token gate, and the session-token gate are the same for bank, casino,
# and whiteboard. They live here so no family depends on another family's module. They use only the
# service's ``sessions``, ``secret``, and ``token_store`` attributes.

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from bbsengine6 import io

from .auth import TokenError, _decode_token
from .errors import (
    CODE_DATABASE_ERROR,
    CODE_INSTANCE_MISMATCH,
    CODE_TOKEN_EXPIRED,
    CODE_TOKEN_REVOKED,
    error_envelope,
    forbidden,
    not_authenticated,
)
from .session import SessionState
from .token_store import TokenStore


def _get_session_for(
    self_ref: "BankService", websocket: Any
) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
    """Look up the SessionState bound to ``websocket``.

    Returns ``(state, None)`` on success or ``(None, error_envelope)``
    when no session is bound (the websocket has not completed
    ``auth``/``reconnect``/``auth_refresh``). Kept as a module-level
    helper (not a method) so it composes cleanly with the
    ``_validate_shape`` / ``access()`` pipeline.
    """
    if websocket is None:
        return None, not_authenticated()
    try:
        ws_id = str(websocket.id)
    except Exception:
        return None, not_authenticated()
    state = self_ref.sessions.get_by_websocket(ws_id)
    if state is None:
        return None, not_authenticated()
    return state, None


def _get_or_bind_session_for(
    self_ref: "BankService",
    websocket: Any,
    message: Dict[str, Any],
) -> Tuple[Optional[SessionState], Optional[Dict[str, Any]]]:
    """Return the session for ``websocket``, lazily binding from a
    valid wire token when no session is bound yet.

    The CLI's ``bank`` tool runs each bank wire call under a fresh
    ``asyncio.run`` (one per subcommand) which closes its event
    loop and forces :class:`BedConnection` to open a new WebSocket
    on the next call. Each new WebSocket is a fresh ``websocket.id``
    in the server's eyes, so without this fallback the session
    registered by the prior ``auth reconnect`` is no longer
    reachable and every bank op returns ``not_authenticated``.

    The fallback mirrors the ``auth reconnect`` handshake: when a
    valid wire token is present, its ``session_id`` / ``moniker`` /
    ``is_sysop`` / ``loginid`` claims are used to either re-bind an
    existing :class:`SessionState` (its WS mapping is updated) or
    synthesize a fresh one if the server's session registry has
    lost the entry (e.g. after a process restart). The wire token
    becomes the new ``state.auth_service_token`` so subsequent
    defense-in-depth checks see a consistent snapshot. The
    validated claims are stashed on ``message["claims"]`` so the
    downstream :func:`bbsengine6.bank.access` call can prefer
    claim-derived ``moniker`` / ``is_sysop`` over the in-memory
    session attributes.

    Returns the same ``(state, err)`` tuple shape as
    :func:`_get_session_for`. ``err`` is set on:

    * No bound session AND no wire token: ``not_authenticated``
      (a caller that never sent ``auth`` / ``reconnect`` AND
      never sent a token -- the legacy unauthenticated case).
    * Bound session absent, wire token present but invalid:
      the envelope from :func:`_validate_token_against_store`
      (``token_invalid`` / ``token_revoked`` / ``instance_mismatch``
      / ``token_expired``).
    """
    state, err = _get_session_for(self_ref, websocket)
    if state is not None:
        return state, None

    wire_token = (message.get("token") or "").strip()
    if not wire_token:
        return None, not_authenticated()

    claims, token_err = _validate_wire_token(self_ref, message)
    if token_err is not None:
        return None, token_err
    if claims is None:
        return None, not_authenticated()

    session_id = (claims.get("session_id") or "").strip()
    if not session_id:
        return None, not_authenticated()

    try:
        ws_id = str(websocket.id)
    except Exception:
        return None, not_authenticated()

    existing = self_ref.sessions.get_by_session(session_id)
    moniker = (claims.get("moniker") or "").strip()
    is_sysop = bool(claims.get("is_sysop", False))
    loginid = claims.get("loginid")

    if existing is not None:
        state = self_ref.sessions.bind(
            session_id,
            ws_id,
            existing.moniker or moniker,
            bool(existing.is_sysop) or is_sysop,
            loginid=existing.loginid or loginid,
        )
    else:
        state = self_ref.sessions.bind(
            session_id,
            ws_id,
            moniker,
            is_sysop,
            loginid=loginid,
        )

    state.auth_service_token = wire_token
    message["claims"] = claims
    return state, None


def _validate_token_against_store(
    self_ref: "BankService", token: str
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Decode ``token`` and check it against the token store + instance.

    Shared core for :func:`_validate_session_token` and
    :func:`_validate_wire_token`. Returns the same ``(claims, None)``
    / ``(None, error_envelope)`` tuple shape.

    A ``token`` of ``""`` / ``None`` returns ``(None, None)`` (no
    token was supplied). An unset ``secret`` / ``token_store`` /
    ``instance_id`` also returns ``(None, None)`` so legacy /
    unit-test callers that constructed the service without token
    wiring degrade gracefully to session-bound authorization.
    """
    token = (token or "").strip()
    if not token:
        return None, None

    secret = getattr(self_ref, "secret", None)
    token_store = getattr(self_ref, "token_store", None)
    instance_id = getattr(self_ref, "instance_id", None)
    if not secret or token_store is None or not instance_id:
        return None, None

    try:
        claims = _decode_token(token, secret)
    except TokenError as e:
        return None, error_envelope(e.code, str(e), recoverable=False)

    store_record = token_store.get(token)
    if store_record is None:
        return None, error_envelope(
            CODE_TOKEN_REVOKED,
            "Token is no longer valid",
            recoverable=False,
        )
    if store_record.bed_instance_id != instance_id:
        token_store.delete(token)
        return None, error_envelope(
            CODE_INSTANCE_MISMATCH,
            "Token was issued by a different bed instance",
            recoverable=False,
        )

    now_fn = getattr(self_ref, "_now", None)
    now = float(now_fn()) if callable(now_fn) else None
    expires_at = store_record.expires_at
    if now is not None and expires_at <= now:
        token_store.delete(token)
        return None, error_envelope(
            CODE_TOKEN_EXPIRED,
            "Token has expired",
            recoverable=True,
        )

    return claims, None


def _validate_session_token(
    self_ref: "BankService", state: SessionState
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Validate ``state.auth_service_token`` if it is set.

    Returns ``(claims, None)`` on success -- ``claims`` is the decoded
    token claims dict, ready to be stashed on ``message["claims"]`` so
    :func:`bbsengine6.bank.access` can prefer claim-derived
    ``moniker`` / ``is_sysop`` over the in-memory session state.

    Returns ``(None, None)`` when the session has no token (e.g. it
    was created outside the auth flow); the caller falls back to the
    existing session-based authorization.

    Returns ``(None, error_envelope)`` on any token failure: the
    caller surfaces the envelope and stops processing. The envelope
    codes mirror the auth service so clients can reuse their
    reconnect logic: ``token_invalid`` for HMAC / shape failures,
    ``token_revoked`` for store misses, ``instance_mismatch`` for
    tokens minted by a different bed instance, ``token_expired`` for
    stale tokens.

    Kept as a module-level helper (not a method) so it composes
    cleanly with the session / shape / access() pipeline.
    """
    return _validate_token_against_store(
        self_ref, getattr(state, "auth_service_token", None)
    )


def _validate_wire_token(
    self_ref: "BankService", message: Dict[str, Any]
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Validate ``message["token"]`` if it is present.

    Companion to :func:`_validate_session_token`. The CLI sends the
    bearer token it read from its ``--token-file`` on every wire
    call; the server validates it independently of (and in
    preference to) the WS-bound session token. This is the
    defense-in-depth path: a token revoked since the WS opened
    cannot drive ledger mutations even if the session-bound
    snapshot is stale.

    Returns ``(None, None)`` when ``message["token"]`` is empty or
    absent so legacy callers (and tests that don't care about the
    wire token) fall through to the session-bound path.
    """
    return _validate_token_against_store(
        self_ref, message.get("token") or ""
    )
