"""Login-time check for whether a member owns any casino table.

The casino client shows ``table update`` only to sysops and table owners, so the
login and reconnect replies carry ``owns_tables``. Bed does not read casino tables
itself. It asks ``engine.casino_read`` with the member's token hash, the same as the
casino table handlers, and checks the owner of each table returned.

Bed's login role is used (``set_role=False``), as in ``login_balance``. Any failure
returns ``False`` and is logged, so a failed read never blocks login.
"""

import json
from typing import Any

from bbsengine6 import database, io


def read_owns_tables(*, args: Any, pool: Any, token_hash: bytes, moniker: str) -> bool:
    """True when ``moniker`` owns at least one casino table visible to the caller."""
    try:
        with database.connect(args, pool=pool, set_role=False) as conn, \
                conn.cursor() as cur:
            cur.execute(
                "SELECT engine.casino_read(%s, %s, %s::jsonb)",
                (token_hash, "list_tables", json.dumps({})),
            )
            row = cur.fetchone()
    except Exception as exc:
        io.echo(f"owns_tables read failed for {moniker}: {exc}", level="warning")
        return False
    if not row or not row[0]:
        return False
    result = row[0]
    if isinstance(result, str):
        result = json.loads(result)
    if not isinstance(result, dict) or not result.get("success"):
        return False
    return any(t.get("owner") == moniker for t in result.get("tables", []))
