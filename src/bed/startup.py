"""
BED startup: run bbsengine6 startup then ensure the bed role exists.

This module is the entry point for ``python -m bed.startup``.  It first
delegates to bbsengine6.startup (which creates the database, core roles,
schema, functions, etc.) and then creates the ``bed`` PostgreSQL role
with LOGIN if it does not already exist, granting it USAGE on the
``engine`` schema.
"""

import sys

from bbsengine6 import database, io
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


def main(args=None):
    """Run bbsengine6 startup, then ensure the bed role exists."""
    if args is None:
        parser = startuplib.buildargs()
        args = parser.parse_args()

    result = startuplib.runmodule(args, "main")
    if result is not True:
        io.echo("bbsengine6 startup failed, skipping bed role setup", level="error")
        return False

    pool = database.getpool(args, dbname=args.databasename)
    with database.connect(args, pool=pool) as conn:
        ok = _ensure_bed_role(args, conn)
        if ok:
            conn.commit()
            io.echo("bed startup complete", level="ok")
        else:
            conn.rollback()
            io.echo("bed startup failed", level="error")
        return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
