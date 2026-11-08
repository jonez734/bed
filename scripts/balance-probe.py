# scripts/balance-probe.py
# Make one bank_balance call to the bed daemon with the signed-in member's token and print
# the balance or the raw exception. Run it as the account that owns the token file:
#   python3 scripts/balance-probe.py
# Reads /run/user/1000/bed.token. Needs bed and bbsengine6 importable.

import asyncio, traceback
from bed.client import get_bed_connection
from bed.client.bank import BedBankClient
from bed.tools import _token
class A:
    debug = False; bed_host = "127.0.0.1"; bed_port = 8765; bed_call_timeout = 10.0
t = _token.read_token_file("/run/user/1000/bed.token") or ""
print("token length:", len(t))
async def m():
    async with get_bed_connection(A()) as c:
        try:
            print("balance:", await BedBankClient(c).get_balance_result("jam", token=t))
        except Exception as e:
            print("raised:", type(e).__name__, "|", e)
            traceback.print_exc(limit=3)
asyncio.run(m())
