"""Stand-alone bank operations script.

The CLI delegates authorization to ``bbsengine6.bank.access`` (the
same module-level function bed.api.bank.BankService uses). This
keeps the policy in one place: the bank module owns who may do
what, and both the WS service and the CLI ask it. The CLI builds a
synthetic SessionState-like object from ``args.moniker`` (resolved
to the current member if not given) and ``args.sysop`` (the
``--sysop`` flag).
"""

import argparse
import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

from bbsengine6 import io, member, database
from bbsengine6.bank import BankService
from bbsengine6.bank import access as _bank_access

from bed.tools import _routing


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

    The bank tool does not authenticate via bed -- it just reads
    ``--moniker`` and ``--sysop``. So the synthetic session is
    populated from those flags plus the resolved moniker (passed
    in by the caller as the function argument, since the CLI
    resolves the current user before invoking any subcommand).
    access() only needs ``.moniker`` and ``.is_sysop``.
    """
    return SimpleNamespace(
        moniker=moniker if moniker else (getattr(args, "moniker", None) or ""),
        is_sysop=bool(getattr(args, "sysop", False)),
    )


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
    ``session_moniker`` overrides ``args.moniker`` for the session;
    pass the resolved moniker (or the moniker argument the
    subcommand received) here.

    The session-bound gate is checked first: if neither
    ``session_moniker`` nor ``args.moniker`` resolves to a non-empty
    string, the subcommand is denied unconditionally. This matches
    the WS handler's ``_require_session`` gate so the two surfaces
    agree on what "unauthenticated" means.
    """
    synth = _make_session(args, moniker=session_moniker)
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
    if args.moniker:
        return args.moniker
    pool = database.getpool(args)
    moniker = member.getcurrentmoniker(args, pool=pool)
    if moniker is None:
        io.echo("Could not determine current user.", level="error")
    return moniker


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

        self._client = BedBankServiceClient(get_bed_connection(args))

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
    """
    try:
        args._backend = _routing.select_backend(args)
    except _routing.BedNotReachable as e:
        io.echo(str(e), level="error")
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
