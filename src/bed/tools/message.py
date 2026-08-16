"""Stand-alone message operations script.

The CLI delegates transport to ``bed.client.messageservice.BedMessageServiceClient``
(the same client ``bed.api.message.MessageService`` talks to on the server
side) and falls back to ``bbsengine6.message.*`` when run with ``--direct``.
This keeps the protocol shape in one place: the message module owns the
verb vocabulary (``subscribe`` / ``unsubscribe`` / ``list_pending`` /
``send`` / ``mark_read`` / ``mark_delivered``) and both the WS service
and the CLI ask it.

The CLI runs in one of two backends:

- ``bed`` (default): the CLI reads the bearer token from ``--token-file``
  (default ``$XDG_RUNTIME_DIR/bed.token`` or ``/tmp/bed-<uid>/bed.token``),
  uses it to call ``auth reconnect`` on the WebSocket so the WS has a
  session bound, and then sends message wire calls on the same WS. The
  server validates the WS-bound session on every op. If no token file
  is present the CLI renders a one-line hint pointing at ``bed auth
  login`` and exits non-zero. ``send`` / ``mark_read`` /
  ``mark_delivered`` are rejected with a clear message: bed's
  MessageService only handles ``message_subscribe`` /
  ``message_unsubscribe`` / ``message_list_pending``; new messages
  flow through the local DB and surface to bed via the
  ``engine_message_recipient`` NOTIFY trigger.
- ``direct`` (``--direct``): the CLI talks to the local DB through
  ``bbsengine6.message.*``. Authentication is skipped; the moniker
  comes from ``--moniker`` or the local member lookup. ``subscribe``
  / ``unsubscribe`` / ``watch`` are rejected in direct mode (no WS
  to bind).

Authorization: the WS ops (``subscribe`` / ``unsubscribe`` /
``list_pending`` / ``watch``) delegate to ``bbsengine6.message.access``
the same way ``bed.api.message.MessageService`` does. The DB-only
ops (``send`` / ``mark_read`` / ``mark_delivered``) are gated by a
local self-or-sysop rule because ``bbsengine6.message.access`` only
recognizes the wire-protocol verbs. The rule matches the existing
per-op policy: a member may target themselves, and a sysop may
target anyone.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from bbsengine6 import io, member, database, message as message_module
from bbsengine6.message import access as _message_access
from bbsengine6.message import render_template

from bed import _version as bed_version
from bed.tools import _routing
from bed.tools import _token


# CLI subcommand -> domain verb understood by bbsengine6.message.access.
# The message module owns the verb vocabulary; this dict is the only
# place the CLI needs to maintain the translation for the WS ops.
_WS_OP: Dict[str, str] = {
    "subscribe": "subscribe",
    "unsubscribe": "unsubscribe",
    "pending": "list_pending",
    "watch": "subscribe",
}


# Backend gate: which subcommands are allowed on which backend.
# ``bed`` mode only handles WS-bound ops (subscribe / unsubscribe /
# list_pending / watch). ``direct`` mode handles the DB-backed ops
# (send / list_pending / mark_read / mark_delivered) and rejects the
# WS-only ones with a one-line operator message. The DB-only
# subcommands (``send`` / ``mark_read`` / ``mark_delivered``) are
# forced to ``direct`` mode in :func:`main_with_args` regardless of
# whether the operator passed ``--direct``; bed has no handler for
# them, so routing through the daemon would just fail.
_BED_ONLY_SUBCMDS = {"subscribe", "unsubscribe", "watch"}
_DIRECT_ONLY_SUBCMDS = {"send", "mark_read", "mark_delivered"}


_BED_UNSUPPORTED_FMT = (
    "'{sub}' operates through the local database; use --direct "
    "or run 'bed auth login' first"
)
_DIRECT_UNSUPPORTED_FMT = (
    "'{sub}' operates through the bed daemon; --direct is unsupported"
)


def buildargs(parentparser: argparse.ArgumentParser) -> None:
    """Register the ``message`` CLI flags on ``parentparser``.

    The ``common`` parent carries the flags every subcommand shares
    (``--moniker``, ``--sysop``, ``--debug``, ``--token-file``) so
    each subparser inherits them. This mirrors the auth tool's
    pattern and lets the operator pass ``--moniker alice`` either
    before or after the subcommand name.
    """
    _routing.build_client_args(parentparser)
    database.buildargs(parentparser, suppress=True)
    parentparser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--moniker",
        default=None,
        help="Target member moniker (defaults to current user)",
    )
    common.add_argument(
        "--sysop",
        action="store_true",
        help="Bypass sysop privilege check",
    )
    _token.build_token_file_arg(common)

    sub = parentparser.add_subparsers(dest="subcommand", required=True)

    sub.add_parser(
        "subscribe",
        parents=[common],
        help="Bind the bed websocket to a moniker for NOTIFY fanout",
    )
    sub.add_parser(
        "unsubscribe",
        parents=[common],
        help="Drop the bed websocket binding for a moniker",
    )
    sub.add_parser(
        "pending",
        parents=[common],
        help="List the pending message queue for a moniker",
    )

    send_p = sub.add_parser(
        "send",
        parents=[common],
        help="Store a new message in the local database",
    )
    send_p.add_argument(
        "--to",
        action="append",
        default=[],
        dest="recipients",
        help="Recipient moniker (repeat for multiple)",
    )
    send_p.add_argument(
        "--channel",
        default="cli.message",
        help="Channel/type name (default: cli.message)",
    )
    send_p.add_argument(
        "--urgency",
        default="ROUTINE",
        choices=["ROUTINE", "IMPORTANT", "URGENT", "CRITICAL"],
        help="Message urgency (default: ROUTINE)",
    )
    send_p.add_argument(
        "--template",
        default=None,
        help="Template body; rendered with --var key=value pairs",
    )
    send_p.add_argument(
        "--content",
        default=None,
        help="Raw content (mutually exclusive with --template)",
    )

    mark_p = sub.add_parser(
        "mark_read",
        parents=[common],
        help="Mark a message as read for a recipient",
    )
    mark_p.add_argument(
        "--message-id",
        type=int,
        required=True,
        help="engine.__message.id to mark read",
    )

    delivered_p = sub.add_parser(
        "mark_delivered",
        parents=[common],
        help="Mark a message as delivered for a recipient",
    )
    delivered_p.add_argument(
        "--message-id",
        type=int,
        required=True,
        help="engine.__message.id to mark delivered",
    )

    sub.add_parser(
        "watch",
        parents=[common],
        help="Subscribe and tail live pushes until interrupted",
    )


def _make_session(args: Any, moniker: str | None = None) -> SimpleNamespace:
    """Build a SessionState-like object for the CLI's access() call.

    Precedence for ``.moniker``: explicit argument > explicit
    ``args.moniker`` flag > claim-derived ``args._session_moniker``
    (set by :func:`_authenticate_ws` from the token's claims).
    Precedence for ``.is_sysop``: explicit ``args.sysop`` flag >
    claim-derived ``args._session_is_sysop``. ``access()`` only
    needs ``.moniker`` and ``.is_sysop``.
    """
    return SimpleNamespace(
        moniker=(
            (moniker or "").strip()
            or getattr(args, "moniker", None)
            or getattr(args, "_session_moniker", None)
            or ""
        ),
        is_sysop=bool(
            getattr(args, "sysop", False)
            or getattr(args, "_session_is_sysop", False)
        ),
    )


def _resolve_actor_moniker(args: Any, fallback: str | None = None) -> str:
    """Return the actor moniker (who is performing the op).

    Precedence:

    1. ``args._session_moniker`` -- claim-derived from the bearer
       token validated by :func:`_authenticate_ws`. When set, this
       wins over everything because the token is the cryptographic
       source of truth for the actor's identity. Matches the
       server's claim-derived authorization path.
    2. ``fallback`` -- the moniker resolved by the caller (e.g.
       ``_resolve_moniker`` for the direct-mode path). Empty when
       no fallback was given.
    3. ``args.moniker`` -- the explicit ``--moniker`` flag. Last
       resort so direct-mode callers that didn't pre-resolve a
       moniker still get the right actor.

    Returns the resolved moniker (may be empty when all three
    sources are unset; callers should treat empty as "no actor"
    and deny).
    """
    return (
        getattr(args, "_session_moniker", "")
        or (fallback or "")
        or getattr(args, "moniker", "")
        or ""
    ).strip()


def _check_access(
    args: Any,
    op: str,
    *,
    session_moniker: str | None = None,
    **message_fields: Any,
) -> bool:
    """Gate a WS subcommand through ``bbsengine6.message.access``.

    Returns True if access is allowed, False otherwise. On False,
    prints a one-line error so the caller can short-circuit.
    ``session_moniker`` is the actor moniker the caller resolved;
    the actual session moniker passed to ``access()`` is
    :func:`_resolve_actor_moniker` -- it prefers the claim-derived
    ``args._session_moniker`` (set by :func:`_authenticate_ws` after
    a successful ``auth reconnect``) over the caller's value so the
    CLI's local authorization agrees with the server's
    claim-derived path.

    The session-bound gate is checked first: if the resolved actor
    moniker is empty, the subcommand is denied unconditionally.
    This matches the WS handler's session gate so the two surfaces
    agree on what "unauthenticated" means.
    """
    actor = _resolve_actor_moniker(args, fallback=session_moniker)
    synth = _make_session(args, moniker=actor)
    if not (synth.moniker or "").strip():
        io.echo(
            f"Operation '{op}' requires an authenticated session.",
            level="error",
        )
        return False
    msg: Dict[str, Any] = {}
    for k, v in message_fields.items():
        if v is None:
            continue
        msg[k] = v
    if _message_access(args, op, session=synth, message=msg):
        return True
    io.echo(
        f"Operation '{op}' is not permitted for this account.",
        level="error",
    )
    return False


def _check_self_or_sysop(
    args: Any,
    op: str,
    actor: str,
    target: str,
) -> bool:
    """Gate a DB-only subcommand with a self-or-sysop rule.

    ``bbsengine6.message.access`` only recognizes the three
    wire-protocol verbs (``subscribe`` / ``unsubscribe`` /
    ``list_pending``). For DB-only ops (``send`` /
    ``mark_read`` / ``mark_delivered``) we apply the same rule
    locally: the actor must be the target, or the actor must be
    sysop. This mirrors the per-op policy for the WS verbs without
    requiring a bbsengine6-side extension.
    """
    if not actor:
        io.echo(
            f"Operation '{op}' requires an authenticated session.",
            level="error",
        )
        return False
    is_sysop = bool(
        getattr(args, "sysop", False)
        or getattr(args, "_session_is_sysop", False)
    )
    if is_sysop:
        return True
    if actor.strip().lower() == (target or "").strip().lower():
        return True
    io.echo(
        f"Operation '{op}' is not permitted for this account.",
        level="error",
    )
    return False


def _resolve_moniker(args: Any) -> str | None:
    """Resolve the actor's moniker for the CLI session.

    Precedence:

    1. Explicit ``args.moniker`` flag.
    2. Claim-derived ``args._session_moniker`` (set by
       :func:`_authenticate_ws` when the token validated).
    3. Local DB lookup via :func:`member.getcurrentmoniker`.

    Returns the resolved moniker or ``None`` if none of the three
    paths produce a non-empty value. The caller is expected to
    surface the failure to the operator.
    """
    if getattr(args, "moniker", None):
        return args.moniker
    session_moniker = getattr(args, "_session_moniker", None)
    if session_moniker:
        return session_moniker
    try:
        pool = database.getpool(args)
    except Exception as e:
        io.echo(f"Could not determine current user: {e}", level="error")
        return None
    moniker = member.getcurrentmoniker(args, pool=pool)
    if moniker is None:
        io.echo(
            "Could not determine current user; pass --moniker "
            "or supply a token via --token-file.",
            level="error",
        )
    return moniker


_MISSING_TOKEN_HINT = (
    "no bearer token found at {path}; run 'bed auth login' first "
    "(or pass --token-file <path>)"
)


def _authenticate_ws(args: Any) -> bool:
    """Bind a session to the bed WebSocket using the saved token.

    Reads the token from ``args.token_file`` (filled in by
    :func:`_token.ensure_token_file_arg` if absent). When the file
    is missing or empty, prints the standard "no bearer token"
    hint and returns False; the caller exits non-zero.

    On a successful ``auth reconnect`` reply, stashes the
    claim-derived ``moniker`` / ``is_sysop`` on ``args`` so the
    local ``_check_access`` mirrors the server's claim-derived
    path. If the server rotated the token (reply.token differs
    from the input), writes the rotated token back to the file so
    subsequent runs (and other tools) pick it up.

    Returns True on success, False on any soft failure (missing
    token, bad credentials, token revoked / expired / invalid /
    instance-mismatch, or transport failure).
    """
    _token.ensure_token_file_arg(args)
    token = _token.read_token_file(args.token_file)
    if not token:
        io.echo(
            _MISSING_TOKEN_HINT.format(path=args.token_file),
            level="error",
        )
        return False

    from bed.client import get_bed_connection
    from bed.client.authservice import BedAuthServiceClient

    client = BedAuthServiceClient(get_bed_connection(args))
    reply = asyncio.run(client.reconnect(token))

    if not reply.get("ok"):
        code = reply.get("code") or "unknown"
        message = reply.get("message") or ""
        if code in (
            "not_authenticated",
            "token_invalid",
            "token_revoked",
            "token_expired",
            "bed_instance_mismatch",
        ):
            io.echo(
                _MISSING_TOKEN_HINT.format(path=args.token_file)
                + f" (server said {code}: {message})",
                level="error",
            )
        else:
            io.echo(f"{code}: {message}".rstrip(), level="error")
        return False

    args._session_moniker = reply.get("moniker", "") or ""
    args._session_is_sysop = bool(reply.get("is_sysop", False))
    rotated_token = (reply.get("token") or "").strip()
    if rotated_token and rotated_token != token:
        try:
            _token._ensure_parent_dir(args.token_file, mode=0o700)
            _token.check_token_file_perms(args.token_file)
        except (OSError, PermissionError) as e:
            io.echo(
                f"could not refresh token file {args.token_file}: {e}",
                level="error",
            )
            return False
        try:
            with open(args.token_file, "w", encoding="utf-8") as f:
                f.write(rotated_token + "\n")
            try:
                os.chmod(args.token_file, 0o600)
            except OSError:
                pass
        except OSError as e:
            io.echo(
                f"could not refresh token file {args.token_file}: {e}",
                level="error",
            )
            return False
    args._resolved_token = rotated_token or token
    return True


def _render_soft_failure(reply: Dict[str, Any]) -> None:
    """Print a one-line error from a soft-failure reply dict."""
    code = reply.get("code") or "unknown"
    message = reply.get("message") or ""
    io.echo(f"{code}: {message}".rstrip(), level="error")


class _BedMessageFacade:
    """Sync wrapper around :class:`BedMessageServiceClient`.

    Bridges the async WebSocket client to the synchronous CLI shape
    so the ``message_*`` call sites don't need to know which backend
    is in use. Soft failures (transport, server ``error`` envelope)
    come back as the same ``{"ok": False, "code": ..., "message":
    "..."}`` shape the tool already renders.
    """

    def __init__(self, args: Any) -> None:
        from bed.client import get_bed_connection
        from bed.client.messageservice import BedMessageServiceClient

        self._client = BedMessageServiceClient(
            get_bed_connection(args)
        )
        self._conn = get_bed_connection(args)

    def _run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def subscribe(self, moniker: str) -> Dict[str, Any]:
        reply = self._run(self._client.subscribe(moniker))
        return reply

    def unsubscribe(self, moniker: str) -> Dict[str, Any]:
        reply = self._run(self._client.unsubscribe(moniker))
        return reply

    def list_pending(self, moniker: str) -> Dict[str, Any]:
        reply = self._run(self._client.list_pending(moniker))
        if reply.get("ok"):
            return {
                "ok": True,
                "moniker": moniker,
                "messages": list(reply.get("messages", [])),
            }
        return {
            "ok": False,
            "code": reply.get("code", "unknown"),
            "message": reply.get("message", ""),
            "messages": [],
        }


def _backend_guard(args: Any, sub: str) -> bool:
    """Reject subcommands the current backend cannot service.

    Returns True if the subcommand is allowed on the current
    backend, False if it was rejected (with a clear one-line error
    already printed).
    """
    backend = getattr(args, "_backend", "")
    if backend == "bed" and sub in _DIRECT_ONLY_SUBCMDS:
        io.echo(_BED_UNSUPPORTED_FMT.format(sub=sub), level="error")
        return False
    if backend == "direct" and sub in _BED_ONLY_SUBCMDS:
        io.echo(_DIRECT_UNSUPPORTED_FMT.format(sub=sub), level="error")
        return False
    return True


def _needs_auth(sub: str) -> bool:
    """Return True if ``sub`` requires a token / WS session bind.

    ``send`` / ``mark_read`` / ``mark_delivered`` / ``pending`` run
    on the local DB in direct mode and don't need a token;
    ``subscribe`` / ``unsubscribe`` / ``watch`` always need a
    session because they touch the bed websocket. ``pending`` in
    bed mode also needs a session because the WS handler requires
    it.
    """
    if sub in _BED_ONLY_SUBCMDS:
        return True
    if sub == "pending":
        return True
    return False


def message_subscribe(args: Any, moniker: str, **kwargs: Any) -> bool:
    op = _WS_OP["subscribe"]
    if not _check_access(
        args, op, session_moniker=moniker, moniker=moniker
    ):
        return False
    svc = _BedMessageFacade(args)
    reply = svc.subscribe(moniker)
    if not reply.get("ok"):
        _render_soft_failure(reply)
        return False
    flag = " (already subscribed)" if reply.get("already_subscribed") else ""
    io.echo(f"subscribed moniker={moniker!r}{flag}")
    return True


def message_unsubscribe(args: Any, moniker: str, **kwargs: Any) -> bool:
    op = _WS_OP["unsubscribe"]
    if not _check_access(
        args, op, session_moniker=moniker, moniker=moniker
    ):
        return False
    svc = _BedMessageFacade(args)
    reply = svc.unsubscribe(moniker)
    if not reply.get("ok"):
        _render_soft_failure(reply)
        return False
    if reply.get("no_subscription"):
        io.echo("no active subscription")
    else:
        io.echo(f"unsubscribed moniker={moniker!r}")
    return True


def _render_pending(messages: List[Dict[str, Any]]) -> None:
    """Print the pending message list in a stable, scannable form."""
    if not messages:
        io.echo("No pending messages.")
        return
    for m in messages:
        urgency = m.get("urgency", "ROUTINE")
        sender = m.get("sender_moniker") or "<system>"
        content = m.get("content", "")
        msg_id = m.get("id", 0)
        status = m.get("status", "pending")
        io.echo(
            f"  #{msg_id}  [{urgency}]  from={sender}  status={status}  "
            f"{content}"
        )


def message_pending(args: Any, moniker: str, **kwargs: Any) -> bool:
    op = _WS_OP["pending"]
    if not _check_access(
        args, op, session_moniker=moniker, moniker=moniker
    ):
        return False
    backend = getattr(args, "_backend", "")
    if backend == "bed":
        svc = _BedMessageFacade(args)
        reply = svc.list_pending(moniker)
        if not reply.get("ok"):
            _render_soft_failure(reply)
            return False
        messages = list(reply.get("messages", []))
    else:
        messages = list(
            message_module.get_pending_messages(moniker, limit=100)
        )
    _render_pending(messages)
    return True


def _resolve_send_content(args: Any) -> str:
    """Return the body the ``send`` subcommand will store.

    ``--content`` is the literal string; ``--template`` is rendered
    with ``--var key=value`` pairs (via :func:`render_template`).
    Exactly one of the two must be set; otherwise the caller
    renders an error and exits non-zero.
    """
    content = (getattr(args, "content", None) or "").strip()
    template = (getattr(args, "template", None) or "").strip()
    if content and template:
        io.echo(
            "--content and --template are mutually exclusive",
            level="error",
        )
        return ""
    if not content and not template:
        io.echo(
            "either --content or --template is required",
            level="error",
        )
        return ""
    if content:
        return content
    return render_template(template, {})


def message_send(args: Any, moniker: str, **kwargs: Any) -> bool:
    actor = _resolve_actor_moniker(args, fallback=moniker)
    recipients: List[str] = list(getattr(args, "recipients", []) or [])
    if not recipients:
        io.echo(
            "at least one --to <moniker> is required",
            level="error",
        )
        return False
    if not _check_self_or_sysop(args, "send", actor, actor):
        return False
    body = _resolve_send_content(args)
    if not body:
        return False
    channel = (getattr(args, "channel", None) or "cli.message").strip()
    urgency = (getattr(args, "urgency", None) or "ROUTINE").strip()
    try:
        result = message_module.store_message_with_checks(
            channel=channel,
            sender_moniker=actor,
            content=body,
            recipient_monikers=recipients,
            urgency=urgency,
        )
    except Exception as e:
        io.echo(f"send failed: {e}", level="error")
        return False
    msg_id = int(result.get("message_id", 0))
    stored = list(result.get("recipients_stored", []) or [])
    blocked = list(result.get("recipients_blocked", []) or [])
    if msg_id <= 0:
        if not result.get("rate_limit_ok", True):
            io.echo(
                f"rate-limited; message not stored "
                f"(blocked={','.join(blocked) or '<none>'})",
                level="error",
            )
            return False
        io.echo(
            "message not stored (system disabled or no recipients)",
            level="error",
        )
        return False
    io.echo(
        f"stored #{msg_id} channel={channel!r} urgency={urgency} "
        f"to={','.join(stored) or '<none>'}"
    )
    if blocked:
        io.echo(f"  blocked: {','.join(blocked)}")
    return True


def _resolve_mark_target(args: Any, actor: str) -> str:
    """Return the recipient moniker for mark_* subcommands.

    Defaults to the actor moniker so ``mark_read 123`` reads
    ``123`` for the caller. Sysops can target any recipient by
    passing ``--moniker`` explicitly.
    """
    explicit = (getattr(args, "moniker", None) or "").strip()
    if explicit:
        return explicit
    return actor


def message_mark_read(args: Any, moniker: str, **kwargs: Any) -> bool:
    actor = _resolve_actor_moniker(args, fallback=moniker)
    msg_id = int(getattr(args, "message_id", 0) or 0)
    if msg_id <= 0:
        io.echo("--message-id must be a positive integer", level="error")
        return False
    target = _resolve_mark_target(args, actor)
    if not _check_self_or_sysop(args, "mark_read", actor, target):
        return False
    try:
        message_module.mark_read(msg_id, target)
    except Exception as e:
        io.echo(f"mark_read failed: {e}", level="error")
        return False
    io.echo(f"marked #{msg_id} read for {target}")
    return True


def message_mark_delivered(
    args: Any, moniker: str, **kwargs: Any
) -> bool:
    actor = _resolve_actor_moniker(args, fallback=moniker)
    msg_id = int(getattr(args, "message_id", 0) or 0)
    if msg_id <= 0:
        io.echo("--message-id must be a positive integer", level="error")
        return False
    target = _resolve_mark_target(args, actor)
    if not _check_self_or_sysop(args, "mark_delivered", actor, target):
        return False
    try:
        message_module.mark_delivered(msg_id, target)
    except Exception as e:
        io.echo(f"mark_delivered failed: {e}", level="error")
        return False
    io.echo(f"marked #{msg_id} delivered for {target}")
    return True


def message_watch(args: Any, moniker: str, **kwargs: Any) -> bool:
    """Subscribe and tail live pushes until interrupted.

    ``watch`` binds the bed websocket to ``moniker`` (same as
    ``subscribe``) and then prints every server-pushed
    ``{"type": "message", ...}`` envelope as it arrives. Exits
    on ``KeyboardInterrupt`` / ``EOFError`` so the operator can
    stop with ``^C`` / ``^D``.
    """
    op = _WS_OP["watch"]
    if not _check_access(
        args, op, session_moniker=moniker, moniker=moniker
    ):
        return False
    svc = _BedMessageFacade(args)
    reply = svc.subscribe(moniker)
    if not reply.get("ok"):
        _render_soft_failure(reply)
        return False

    def _push(msg: Dict[str, Any]) -> None:
        if msg.get("type") != "message":
            return
        msg_id = msg.get("message_id", "?")
        urgency = msg.get("urgency", "ROUTINE")
        status = msg.get("status", "pending")
        recipient = msg.get("recipient_moniker") or moniker
        io.echo(
            f"  msg #{msg_id}  [{urgency}]  status={status}  "
            f"to={recipient}"
        )

    async def _tail() -> None:
        await svc._conn.subscribe(_push)
        try:
            while True:
                await asyncio.sleep(0.25)
        finally:
            try:
                await svc._conn.unsubscribe(_push)
            except Exception:
                pass

    io.echo(f"watching moniker={moniker!r}; Ctrl-C to stop")
    try:
        asyncio.run(_tail())
    except KeyboardInterrupt:
        pass
    try:
        svc.unsubscribe(moniker)
    except Exception:
        pass
    io.echo("stopped watching")
    return True


# CLI subcommand -> handler. Mirrors the bank tool's dispatch dict.
_HANDLERS = {
    "subscribe": message_subscribe,
    "unsubscribe": message_unsubscribe,
    "pending": message_pending,
    "send": message_send,
    "mark_read": message_mark_read,
    "mark_delivered": message_mark_delivered,
    "watch": message_watch,
}


def main_with_args(args: Any) -> Optional[bool]:
    """Run the message subcommand against a pre-parsed args object.

    Split out from ``main()`` so tests can drive the CLI without
    going through argparse. Returns the subcommand's success flag,
    or ``None`` for early exits (``BedNotReachable`` /
    backend-guard / missing moniker / missing token).

    In ``bed`` mode, :func:`_authenticate_ws` is invoked before
    :func:`_resolve_moniker` so that the claim-derived moniker can
    satisfy the moniker-resolution precedence when neither
    ``--moniker`` nor a local-DB lookup is available. In
    ``direct`` mode authentication is skipped; the moniker must
    come from ``--moniker`` or the local DB.
    """
    sub = getattr(args, "subcommand", None)

    if sub in _DIRECT_ONLY_SUBCMDS:
        args.direct = True

    try:
        args._backend = _routing.select_backend(args)
    except _routing.BedNotReachable as e:
        io.echo(str(e), level="error")
        return None

    if not _backend_guard(args, sub):
        return False

    if getattr(args, "_backend", "") == "bed" and _needs_auth(sub):
        if not _authenticate_ws(args):
            return None

    moniker = _resolve_moniker(args)
    if moniker is None:
        return None

    handler = _HANDLERS.get(sub)
    if handler is None:
        io.echo(f"unknown subcommand {sub!r}", level="error")
        return False

    try:
        return handler(args, moniker)
    except KeyboardInterrupt:
        io.echo("{/all}{restorecursor}*INTR*")
        return False
    except EOFError:
        io.echo("{/all}{restorecursor}*EOF*")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="message",
        description=(
            f"bed message CLI (v{bed_version.__version__}) "
            "-- subscribe, send, list pending"
        ),
    )
    buildargs(parser)
    args = parser.parse_args()
    io.echo(f"{args=}", level="debug")
    ok = main_with_args(args)
    if ok is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
