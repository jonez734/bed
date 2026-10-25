"""Login-time balance read through bed's definer function.

bbsengine6's getcredits() reads bank.__account on a connection that inherits the
per-request member role. bed reads the balance through engine.bed_bank_balance on
a connection that keeps bed's login role (set_role=False), the same as the bank
handlers.
"""

from typing import Any

from bbsengine6 import database


def read_login_balance(*, args: Any, pool: Any, token_hash: bytes, moniker: str) -> int:
    """Return the member's balance, or 0 when the account does not exist."""
    with database.connect(args, pool=pool, set_role=False) as conn, \
            conn.cursor() as cur:
        cur.execute(
            "SELECT account_exists, balance FROM engine.bed_bank_balance(%s, %s)",
            (token_hash, moniker),
        )
        row = cur.fetchone()
    if not row or not row[0]:
        return 0
    return int(row[1])
