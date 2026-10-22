"""The password login reply carries the bed's configured name, and the client keeps it."""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

from bed.api import auth as auth_mod
from bed.client.authservice import BedAuthServiceClient


def test_login_reply_includes_bed_name():
    cls = next(v for v in vars(auth_mod).values() if inspect.isclass(v) and hasattr(v, "_handle_auth"))
    src = inspect.getsource(cls._handle_auth)
    assert "bed_name" in src


def test_client_keeps_bed_name_from_login_reply():
    conn = MagicMock()
    conn.send = AsyncMock(return_value={"type": "auth_result", "success": True, "moniker": "jam",
                                        "token": "t", "session_id": "s", "expires_at": "x",
                                        "balance": 1, "last_login": None, "bed_name": "zoid6"})
    r = asyncio.run(BedAuthServiceClient(conn).login("jam", "pw"))
    assert r["bed_name"] == "zoid6"


def test_client_reports_no_bed_name_for_an_older_bed():
    conn = MagicMock()
    conn.send = AsyncMock(return_value={"type": "auth_result", "success": True, "moniker": "jam",
                                        "token": "t", "session_id": "s", "expires_at": "x", "balance": 1})
    r = asyncio.run(BedAuthServiceClient(conn).login("jam", "pw"))
    assert r["bed_name"] is None
