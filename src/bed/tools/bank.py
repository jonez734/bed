"""Stand-alone bank operations script.

The CLI delegates authorization to ``bbsengine6.bank.access`` (the
same module-level function bed.api.bank.BankService uses). This
keeps the policy in one place: the bank module owns who may do
what, and both the WS service and the CLI ask it. The CLI builds a
synthetic SessionState-like object from ``args.moniker`` (resolved
to the current member if not given) and ``args.sysop`` (the
``--sysop`` flag).

Authentication: the bank tool runs in one of two backends:

- ``bed`` (default): the CLI reads the bearer token from
  ``--token-file`` (default ``$XDG_RUNTIME_DIR/bed.token`` or
  ``/tmp/bed-<uid>/bed.token``), uses it to call ``auth reconnect``
  on the WebSocket so the WS has a session bound, and then sends the
  same token on every bank wire call. The server validates the wire
  token on every op (defense-in-depth) in addition to the
  WS-bound session token. If no token file is present the CLI
  renders a one-line hint pointing at ``bed auth login`` and exits
  non-zero.
- ``direct`` (``--direct``): the CLI talks to the local DB through
  ``bbsengine6.bank.BankService`` and the local authorization is
  gated by ``--moniker`` / ``--sysop`` only. No token is required.
"""

import argparse
import asyncio
import os
from types import SimpleNamespace
from typing import Any, Dict, List

from bbsengine6 import io, member, database
from bbsengine6.bank import BankService
from bbsengine6.bank import access as _bank_access

from bed.tools import _routing
from bed.tools import _token


def buildargs(parentparser: argparse.ArgumentParser) -> None:
    _routing.build_client_args(parentparser)
    database.buildargs(parentparser, suppress=True)
    parentparser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parentparser.add_argument(
        "--moniker",
        default=None,
        help="Target member moniker (defaults to current user)",
    )
    parentparser.add_argument(
        "--sysop", action="store_true", help="Bypass sysop privilege check"
    )
    _token.build_token_file_arg(parentparser)


# Subcommand -> domain verb (op) understood by bbsengine6.bank.access.
# The bank module owns the verb vocabulary; this dict is the only
# place the CLI needs to maintain the translation.
_SUBCMD_TO_OP: Dict[str, str] = {
    "balance": "balance",
    "add": "add",
    "remove": "remove",
    "history": "history",
    "transfer": "transfer",
    "approve": "approve",
    "reject": "reject",
    "pending": "pending",
    "list_all": "list_all",
}


def _make_session(args, moniker: str | None = None) -> SimpleNamespace:
    """Build a SessionState-like object for the CLI's access() call.

    Precedence for ``.moniker``: explicit argument > explicit
    ``args.moniker`` flag > claim-derived ``args._session_moniker``
    (set by :func:`_authenticate_ws` from the token's claims).
    Precedence for ``.is_sysop``: explicit ``args.sysop`` flag >
    claim-derived ``args._session_is_sysop``. access() only needs
    ``.moniker`` and ``.is_sysop``.
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


def _resolve_actor_moniker(args, fallback: str | None = None) -> str:
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


# Map CLI-side keyword argument names to the wire-protocol field
# names that bbsengine6.bank.access() reads. ``from`` is a Python
# keyword, so the CLI uses ``from_``; the bank module expects
# ``message["from"]``.
_FIELD_ALIASES = {"from_": "from"}


def _check_access(
    args, op: str, *, session_moniker: str | None = None, **message_fields: Any
) -> bool:
    """Gate a CLI subcommand through ``bbsengine6.bank.access``.

    Returns True if access is allowed, False otherwise. On False,
    prints a one-line error so the caller can short-circuit.
    ``session_moniker`` is the actor moniker the caller resolved;
    the actual session moniker passed to ``access()`` is
    :func:`_resolve_actor_moniker` -- it prefers the claim-derived
    ``args._session_moniker`` (set by :func:`_authenticate_ws` after
    a successful ``auth reconnect``) over the caller's value so
    the CLI's local authorization agrees with the server's
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
    msg = {}
    for k, v in message_fields.items():
        if v is None:
            continue
        wire_key = _FIELD_ALIASES.get(k, k)
        msg[wire_key] = v
    if _bank_access(args, op, session=synth, message=msg):
        return True
    io.echo(
        f"Operation '{op}' is not permitted for this account.",
        level="error",
    )
    return False


def _resolve_moniker(args) -> str | None:
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


def _authenticate_ws(args) -> bool:
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


def _resolve_call_token(args) -> str:
    """Return the bearer token to inject on every bank wire message.

    Stashes the value on ``args`` on first call so subsequent calls
    return the same string without re-reading the file. The token
    is set by :func:`_authenticate_ws` after a successful reconnect
    (either the rotated token from the server reply, or the input
    token if no rotation occurred). When the CLI runs in ``--direct``
    mode or otherwise skipped authentication, returns ``""`` so the
    client falls back to the WS-bound session token path.
    """
    cached = getattr(args, "_resolved_token", None)
    if cached is not None:
        return cached
    _token.ensure_token_file_arg(args)
    cached = _token.read_token_file(args.token_file)
    args._resolved_token = cached
    return cached


def _resolve_loginids(args, monikers: List[str]) -> Dict[str, str]:
    """Map monikers to ``engine.__member.loginid`` for display.

    Returns an empty dict if the pool cannot be built (e.g. the tool is
    driven from tests with no real DB) or if a per-row lookup fails.
    Callers fall back to the raw moniker when the dict has no entry
    for the actor.
    """
    pool = None
    try:
        pool = database.getpool(args)
    except Exception:
        return {}
    result: Dict[str, str] = {}
    for m in monikers:
        if not m or m in result:
            continue
        try:
            rec = member.getbymoniker(args, m, fields="loginid", pool=pool)
        except Exception:
            continue
        if isinstance(rec, dict):
            val = rec.get("loginid")
            if isinstance(val, str) and val:
                result[m] = val
    return result


class _BedBankFacade:
    """Sync wrapper around :class:`BedBankServiceClient`.

    Bridges the async WebSocket client to the synchronous
    :class:`bbsengine6.bank.BankService` shape so the ``bank_*`` call
    sites don't need to know which backend is in use. Soft failures
    (transport, server ``error`` envelope) come back as the same
    ``{"success": False, "message": "..."}`` shape the tool already
    renders.
    """

    def __init__(self, args: Any) -> None:
        from bed.client import get_bed_connection
        from bed.client.bankservice import BedBankServiceClient

        self._client = BedBankServiceClient(
            get_bed_connection(args), token=_resolve_call_token(args)
        )

    def _run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def get_balance(self, moniker: str) -> int:
        reply = self._run(self._client.get_balance(moniker))
        if not reply.get("ok"):
            return 0
        return int(reply.get("balance", 0))

    def add_funds(
        self,
        moniker: str,
        amount: int,
        transaction_type: str = "credit",
        description: str = "",
    ) -> dict:
        wire_desc = description or transaction_type
        reply = self._run(
            self._client.add_funds(moniker, amount, wire_desc)
        )
        if reply.get("ok"):
            return {
                "success": True,
                "message": f"Added {amount} to {moniker}",
                "new_balance": int(reply.get("new_balance", 0)),
            }
        return {"success": False, "message": reply.get("message", "Failed.")}

    def remove_funds(
        self,
        moniker: str,
        amount: int,
        transaction_type: str = "debit",
        description: str = "",
    ) -> dict:
        wire_desc = description or transaction_type
        reply = self._run(
            self._client.remove_funds(moniker, amount, wire_desc)
        )
        if reply.get("ok"):
            return {
                "success": True,
                "message": f"Removed {amount} from {moniker}",
                "new_balance": int(reply.get("new_balance", 0)),
            }
        return {"success": False, "message": reply.get("message", "Failed.")}

    def transfer(
        self,
        from_moniker: str,
        to_moniker: str,
        amount: int,
        requested_by: str,
    ) -> dict:
        reply = self._run(
            self._client.transfer(
                from_moniker, to_moniker, amount, requested_by
            )
        )
        if reply.get("ok"):
            return {
                "success": True,
                "transfer_id": int(reply.get("transfer_id", 0)),
                "message": reply.get("message", ""),
            }
        return {
            "success": False,
            "message": reply.get("message", "Transfer failed."),
        }

    def get_pending_transfers(
        self, moniker: str = "", is_sysop: bool = False
    ) -> List[dict]:
        reply = self._run(
            self._client.get_pending_transfers(moniker, is_sysop)
        )
        if reply.get("ok"):
            return list(reply.get("transfers", []))
        return []

    def approve_transfer(
        self, transfer_id: int, responded_by: str
    ) -> dict:
        reply = self._run(
            self._client.approve_transfer(transfer_id, responded_by)
        )
        if reply.get("ok"):
            return {
                "success": True,
                "transfer_id": int(reply.get("transfer_id", 0)),
                "from_balance": int(reply.get("from_balance", 0)),
                "to_balance": int(reply.get("to_balance", 0)),
                "message": f"approved #{transfer_id}",
            }
        return {
            "success": False,
            "message": reply.get("message", "Approval failed."),
        }

    def reject_transfer(
        self, transfer_id: int, responded_by: str
    ) -> dict:
        reply = self._run(
            self._client.reject_transfer(transfer_id, responded_by)
        )
        if reply.get("ok"):
            return {
                "success": True,
                "transfer_id": int(reply.get("transfer_id", 0)),
                "message": f"rejected #{transfer_id}",
            }
        return {
            "success": False,
            "message": reply.get("message", "Rejection failed."),
        }

    def get_history(self, moniker: str, limit: int = 50) -> List[dict]:
        reply = self._run(self._client.get_history(moniker, limit))
        if reply.get("ok"):
            return list(reply.get("transactions", []))
        return []

    def list_all(self) -> List[dict]:
        reply = self._run(self._client.list_all())
        if reply.get("ok"):
            return list(reply.get("accounts", []))
        return []


def _bank_service(args: Any) -> Any:
    backend = getattr(args, "_backend", None)
    if backend == "bed":
        return _BedBankFacade(args)
    return BankService(args)


def bank_balance(args, moniker: str, **kwargs) -> bool:
    if not _check_access(
        args, "balance", session_moniker=moniker, moniker=moniker
    ):
        return False
    svc = _bank_service(args)
    balance = svc.get_balance(moniker)
    io.echo(f"{moniker}: {balance}")
    return True


def bank_add(args, moniker: str, **kwargs) -> bool:
    amount = io.inputinteger("Amount to add: ")
    if amount is None or amount <= 0:
        io.echo("Invalid amount.", level="error")
        return False
    if not _check_access(
        args, "add", session_moniker=moniker, moniker=moniker, amount=amount
    ):
        return False
    svc = _bank_service(args)
    result = svc.add_funds(moniker, amount, transaction_type="credit")
    if result.get("success"):
        io.echo(f"{result['message']}  New balance: {result['new_balance']}")
    else:
        io.echo(result.get("message", "Failed."), level="error")
    return result.get("success", False)


def bank_remove(args, moniker: str, **kwargs) -> bool:
    amount = io.inputinteger("Amount to withdraw: ")
    if amount is None or amount <= 0:
        io.echo("Invalid amount.", level="error")
        return False
    if not _check_access(
        args, "remove", session_moniker=moniker, moniker=moniker, amount=amount
    ):
        return False
    svc = _bank_service(args)
    result = svc.remove_funds(moniker, amount, transaction_type="debit")
    if result.get("success"):
        io.echo(f"{result['message']}  New balance: {result['new_balance']}")
    else:
        io.echo(result.get("message", "Failed."), level="error")
    return result.get("success", False)


def bank_transfer(args, moniker: str, **kwargs) -> bool:
    to_moniker = io.inputstring("Transfer to moniker: ")
    if not to_moniker:
        io.echo("No moniker entered.", level="error")
        return False
    amount = io.inputinteger("Amount: ")
    if amount is None or amount <= 0:
        io.echo("Invalid amount.", level="error")
        return False
    if not _check_access(
        args,
        "transfer",
        session_moniker=moniker,
        from_=moniker,
        to=to_moniker,
        amount=amount,
        requested_by=moniker,
    ):
        return False
    svc = _bank_service(args)
    result = svc.transfer(moniker, to_moniker, amount, moniker)
    if result.get("success"):
        io.echo(result["message"])
    else:
        io.echo(result.get("message", "Transfer failed."), level="error")
    return result.get("success", False)


def bank_pending(args, moniker: str, is_sysop: bool = False, **kwargs) -> bool:
    if not _check_access(
        args, "pending", session_moniker=moniker, moniker=moniker
    ):
        return False
    svc = _bank_service(args)
    transfers = svc.get_pending_transfers(moniker, is_sysop=is_sysop)
    if not transfers:
        io.echo("No pending transfers.")
        return True
    loginids = _resolve_loginids(args, [t.get("requestedby", "") for t in transfers])
    for t in transfers:
        requested_by = t.get("requestedby", "")
        io.echo(
            f"  #{t['id']}  {t['from_moniker']} -> {t['to_moniker']}  "
            f"amount={t['amount']}  by={loginids.get(requested_by, requested_by)}  "
            f"at={t['requestedat']}"
        )
    return True


def bank_approve(args, moniker: str, **kwargs) -> bool:
    transfer_id = io.inputinteger("Transfer ID to approve: ")
    if transfer_id is None:
        io.echo("Invalid ID.", level="error")
        return False
    if not _check_access(
        args,
        "approve",
        session_moniker=moniker,
        transfer_id=transfer_id,
        responded_by=moniker,
    ):
        return False
    svc = _bank_service(args)
    result = svc.approve_transfer(transfer_id, moniker)
    if result.get("success"):
        io.echo(result["message"])
    else:
        io.echo(result.get("message", "Approval failed."), level="error")
    return result.get("success", False)


def bank_reject(args, moniker: str, **kwargs) -> bool:
    transfer_id = io.inputinteger("Transfer ID to reject: ")
    if transfer_id is None:
        io.echo("Invalid ID.", level="error")
        return False
    if not _check_access(
        args,
        "reject",
        session_moniker=moniker,
        transfer_id=transfer_id,
        responded_by=moniker,
    ):
        return False
    svc = _bank_service(args)
    result = svc.reject_transfer(transfer_id, moniker)
    if result.get("success"):
        io.echo(result["message"])
    else:
        io.echo(result.get("message", "Rejection failed."), level="error")
    return result.get("success", False)


def bank_history(args, moniker: str, **kwargs) -> bool:
    if not _check_access(
        args, "history", session_moniker=moniker, moniker=moniker
    ):
        return False
    svc = _bank_service(args)
    txns = svc.get_history(moniker)
    if not txns:
        io.echo("No transactions.")
        return True
    loginids = _resolve_loginids(args, [t.get("membermoniker", "") for t in txns])
    for t in txns:
        actor = t.get("membermoniker", "")
        io.echo(
            f"  #{t['id']}  {t['transactiontype']}  amount={t['amount']}  "
            f"{t['description']}  by={loginids.get(actor, actor)}  "
            f"at={t['dateposted']}"
        )
    return True


def bank_list_all(args, moniker: str, **kwargs) -> bool:
    if not _check_access(args, "list_all", session_moniker=moniker):
        return False
    svc = _bank_service(args)
    rows = svc.list_all()
    if not rows:
        io.echo("No accounts.")
        return True
    for r in rows:
        io.echo(f"  {r['moniker']}: {r['balance']}")
    return True


def menu(args, moniker: str) -> bool:
    is_sysop = getattr(args, "sysop", False)

    while True:
        cmd = io.inputchoice(
            "{var:promptcolor}[B]alance  [A]dd  [W]ithdraw  [T]ransfer  "
            "[P]ending  [H]istory  [L]ist all  [Q]uit: {var:inputcolor}",
            "b,a,w,t,p,h,l,q",
            default="q",
            args=args,
        )

        if cmd == "B":
            bank_balance(args, moniker)
        elif cmd == "A":
            bank_add(args, moniker)
        elif cmd == "W":
            bank_remove(args, moniker)
        elif cmd == "T":
            bank_transfer(args, moniker)
        elif cmd == "P":
            bank_pending(args, moniker, is_sysop=is_sysop)
        elif cmd == "H":
            bank_history(args, moniker)
        elif cmd == "L":
            bank_list_all(args, moniker)
        elif cmd == "Q":
            break

    return True


def main_with_args(args) -> None:
    """Run the bank menu loop against a pre-parsed args object.

    Split out from ``main()`` so tests can drive the menu without
    going through argparse. ``main()`` is just ``parse_args`` +
    ``main_with_args``.

    In ``bed`` mode, :func:`_authenticate_ws` is invoked before
    :func:`_resolve_moniker` so that the claim-derived moniker can
    satisfy the moniker-resolution precedence when neither
    ``--moniker`` nor a local-DB lookup is available. In ``direct``
    mode authentication is skipped; the moniker must come from
    ``--moniker`` or the local DB.
    """
    try:
        args._backend = _routing.select_backend(args)
    except _routing.BedNotReachable as e:
        io.echo(str(e), level="error")
        return

    if getattr(args, "_backend", "") == "bed":
        if not _authenticate_ws(args):
            return

    moniker = _resolve_moniker(args)
    if moniker is None:
        return

    try:
        menu(args, moniker)
    except KeyboardInterrupt:
        io.echo("{/all}{restorecursor}*INTR*")
    except EOFError:
        io.echo("{/all}{restorecursor}*EOF*")


def main() -> None:
    parser = argparse.ArgumentParser("bank")
    buildargs(parser)
    args = parser.parse_args()
    io.echo(f"{args=}", level="debug")
    main_with_args(args)


if __name__ == "__main__":
    main()
