"""Stand-alone bank operations script."""

import argparse

from bbsengine6 import io, member, database
from bbsengine6.bank import BankService


def buildargs(parentparser: argparse.ArgumentParser) -> None:
    database.buildargs(parentparser)
    parentparser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )
    parentparser.add_argument(
        "--moniker",
        default=None,
        help="Target member moniker (defaults to current user)",
    )
    # TODO: wire in sysop check
    parentparser.add_argument(
        "--sysop", action="store_true", help="Bypass sysop privilege check"
    )


def _resolve_moniker(args) -> str | None:
    if args.moniker:
        return args.moniker
    pool = database.getpool(args)
    moniker = member.getcurrentmoniker(args, pool=pool)
    if moniker is None:
        io.echo("Could not determine current user.", level="error")
    return moniker


def _bank_service(args) -> BankService:
    return BankService(args)


def bank_balance(args, moniker: str, **kwargs) -> bool:
    svc = _bank_service(args)
    balance = svc.get_balance(moniker)
    io.echo(f"{moniker}: {balance}")
    return True


def bank_add(args, moniker: str, **kwargs) -> bool:
    svc = _bank_service(args)
    amount = io.inputinteger("Amount to add: ")
    if amount is None or amount <= 0:
        io.echo("Invalid amount.", level="error")
        return False
    result = svc.add_funds(moniker, amount, transaction_type="credit")
    if result.get("success"):
        io.echo(f"{result['message']}  New balance: {result['new_balance']}")
    else:
        io.echo(result.get("message", "Failed."), level="error")
    return result.get("success", False)


def bank_remove(args, moniker: str, **kwargs) -> bool:
    svc = _bank_service(args)
    amount = io.inputinteger("Amount to withdraw: ")
    if amount is None or amount <= 0:
        io.echo("Invalid amount.", level="error")
        return False
    result = svc.remove_funds(moniker, amount, transaction_type="debit")
    if result.get("success"):
        io.echo(f"{result['message']}  New balance: {result['new_balance']}")
    else:
        io.echo(result.get("message", "Failed."), level="error")
    return result.get("success", False)


def bank_transfer(args, moniker: str, **kwargs) -> bool:
    svc = _bank_service(args)
    to_moniker = io.inputstring("Transfer to moniker: ")
    if not to_moniker:
        io.echo("No moniker entered.", level="error")
        return False
    amount = io.inputinteger("Amount: ")
    if amount is None or amount <= 0:
        io.echo("Invalid amount.", level="error")
        return False
    result = svc.transfer(moniker, to_moniker, amount, moniker)
    if result.get("success"):
        io.echo(result["message"])
    else:
        io.echo(result.get("message", "Transfer failed."), level="error")
    return result.get("success", False)


def bank_pending(args, moniker: str, is_sysop: bool = False, **kwargs) -> bool:
    svc = _bank_service(args)
    transfers = svc.get_pending_transfers(moniker, is_sysop=is_sysop)
    if not transfers:
        io.echo("No pending transfers.")
        return True
    for t in transfers:
        io.echo(
            f"  #{t['id']}  {t['from_moniker']} -> {t['to_moniker']}  "
            f"amount={t['amount']}  by={t['requestedby']}  "
            f"at={t['requestedat']}"
        )
    return True


def bank_approve(args, moniker: str, **kwargs) -> bool:
    svc = _bank_service(args)
    transfer_id = io.inputinteger("Transfer ID to approve: ")
    if transfer_id is None:
        io.echo("Invalid ID.", level="error")
        return False
    result = svc.approve_transfer(transfer_id, moniker)
    if result.get("success"):
        io.echo(result["message"])
    else:
        io.echo(result.get("message", "Approval failed."), level="error")
    return result.get("success", False)


def bank_reject(args, moniker: str, **kwargs) -> bool:
    svc = _bank_service(args)
    transfer_id = io.inputinteger("Transfer ID to reject: ")
    if transfer_id is None:
        io.echo("Invalid ID.", level="error")
        return False
    result = svc.reject_transfer(transfer_id, moniker)
    if result.get("success"):
        io.echo(result["message"])
    else:
        io.echo(result.get("message", "Rejection failed."), level="error")
    return result.get("success", False)


def bank_history(args, moniker: str, **kwargs) -> bool:
    svc = _bank_service(args)
    txns = svc.get_history(moniker)
    if not txns:
        io.echo("No transactions.")
        return True
    for t in txns:
        io.echo(
            f"  #{t['id']}  {t['transactiontype']}  amount={t['amount']}  "
            f"{t['description']}  by={t['membermoniker']}  at={t['dateposted']}"
        )
    return True


def bank_list_all(args, moniker: str, **kwargs) -> bool:
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
