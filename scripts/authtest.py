# 1. Does jam exist in the schema the bed server uses?
from bbsengine6 import database
from bbsengine6.console.lib import buildargs
args = buildargs().parse_args([])
print("databaseschema:", getattr(args, "databaseschema", None))
pool = database.getpool(args)
try:
    with database.connect(args, pool=pool) as conn, database.cursor(conn) as cur:
        cur.execute(
            database.query(
                "select moniker, (password is not null) as has_pw from $engine.member where moniker=%s"
            ),
            ("jam",),
        )
        print("jam row:", cur.fetchone())
finally:
    pool.close()
# 2. Round-trip the password you typed (REPLACE "the-password-you-just-typed" first)
from bbsengine6 import member
pool = database.getpool(args)
try:
    print(
        "checkpassword result:",
        member.checkpassword(
            args, "12345", membermoniker="jam", pool=pool
        ),
    )
finally:
    pool.close()
# 3. Clear stale pyc just in case
import shutil, pathlib
for p in pathlib.Path("/home/jam/.venv/lib64/python3.12/site-packages/bbsengine6").rglob(
    "__pycache__"
):
    shutil.rmtree(p, ignore_errors=True)
print("pyc cleared")
