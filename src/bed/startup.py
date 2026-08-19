"""
BED startup: run bbsengine6 startup then ensure the bed role exists.

This module is the entry point for ``python -m bed.startup``.  It first
delegates to bbsengine6.startup (which creates the database, core roles,
schema, functions, etc.) and then creates the ``bed`` PostgreSQL role
with LOGIN if it does not already exist, granting it USAGE on the
``engine`` schema.
"""

import sys

import psycopg

from bbsengine6 import database, io, module as bbsmodule
from bbsengine6.startup import lib as startuplib


BED_ROLE = "bed"


def _ensure_bed_role(args, conn):
    """Create the ``bed`` role with LOGIN if it does not exist, and grant
    it USAGE on the ``engine`` schema."""
    io.echo(
        f"{{var:labelcolor}}role {{var:valuecolor}}{BED_ROLE}{{var:labelcolor}}: ",
        end="",
    )
    if database.rolexists(args, BED_ROLE, conn=conn) is False:
        io.echo("create ", end="")
        if database.createrol(
            args,
            BED_ROLE,
            conn=conn,
            superuser=False,
            login=True,
            createdb=False,
            createrole=False,
        ) is False:
            io.echo("{{level.error}} fail ", level="error")
            return False
        io.echo("{{level.ok}} ok  ")
    else:
        io.echo("{{level.ok}} ok  ", level="ok")

    io.echo(
        f"{{var:labelcolor}}schema priv {{var:valuecolor}}engine{{var:labelcolor}}"
        f" -> {{var:valuecolor}}{BED_ROLE}{{var:labelcolor}}: ",
        end="",
    )
    if database.manage_schema_priv(
        args, "grant", "usage", "engine", BED_ROLE, conn=conn
    ) is False:
        io.echo("{{level.error}} fail ", level="error")
        return False
    io.echo("{{level.ok}} ok  ")
    return True


def ensure_startup(args):
    """Run bbsengine6 startup, ensure the bed role exists, then bootstrap
    the casino schema.

    Idempotent: safe to call repeatedly.  Returns True on success, False
    on failure.  Non-interactive: does not parse arguments or call
    sys.exit.

    Connection-level failures (database unreachable, missing database,
    connection refused, OS-level socket errors, psycopg OperationalError
    when the target database is missing) are caught at the top level
    and rendered as a one-line friendly message via
    :func:`bbsengine6.io.echo` with ``level="error"`` so the
    ``bin/bed-startup`` shim exits cleanly with a useful message rather
    than a Python traceback.
    """
    try:
        result = startuplib.runmodule(args, "main")
    except (
        ConnectionError,
        TimeoutError,
        OSError,
        psycopg.OperationalError,
    ) as exc:
        io.echo(
            f"bed-startup: cannot reach database: {exc}",
            level="error",
        )
        return False
    if result is not True:
        io.echo("bbsengine6 startup failed, skipping bed role setup", level="error")
        return False

    try:
        pool = database.getpool(args, dbname=args.databasename)
    except (
        ConnectionError,
        TimeoutError,
        OSError,
        psycopg.OperationalError,
    ) as exc:
        io.echo(
            f"bed-startup: cannot build database pool: {exc}",
            level="error",
        )
        return False
    try:
        with database.connect(args, pool=pool) as conn:
            ok = _ensure_bed_role(args, conn)
            if not ok:
                conn.rollback()
                io.echo("bed role setup failed", level="error")
                return False
            conn.commit()
    except (
        ConnectionError,
        TimeoutError,
        OSError,
        psycopg.OperationalError,
    ) as exc:
        io.echo(
            f"bed-startup: database connection failed: {exc}",
            level="error",
        )
        return False
    # conn released; casino.startup.main opens its own pool/conn lifecycle.

    casino_result = bbsmodule.runmodule(args, "casino.startup.main")
    if casino_result is not True:
        io.echo("casino startup failed", level="error")
        return False

    io.echo("bed startup complete", level="ok")
    return True


def main(args=None):
    """CLI entry point for ``bed-startup`` or ``python -m bed.startup``."""
    if args is None:
        parser = startuplib.buildargs()
        args = parser.parse_args()
    return ensure_startup(args)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
