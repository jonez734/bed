#!/usr/bin/env python3
# bed/tests/test_message_client_auth.py
# Message ops carry the bearer token; rotations are written by the owner only;
# a non-owner re-reads the token file once on not_authenticated.

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

from bed.client import messageservice as ms


def _write_token(path, value):
    """Write a token file with the 0600 mode bed requires."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value)
    os.chmod(path, 0o600)


def _conn(replies):
    conn = MagicMock(name="conn")
    conn.send = AsyncMock(side_effect=replies)
    return conn


def test_every_op_carries_token():
    conn = _conn([{"ok": True, "messages": []}])
    client = ms.BedMessageServiceClient(conn, token="tok-1")
    asyncio.run(client.list_pending("alice"))
    sent = conn.send.await_args.args[0]
    assert sent["token"] == "tok-1"
    assert sent["type"] == "message_list_pending"


def test_owner_writes_rotated_token(tmp_path):
    path = str(tmp_path / "tok")
    conn = _conn([{"ok": True, "messages": [], "token": "tok-2"}])
    client = ms.BedMessageServiceClient(conn, token="tok-1", token_file=path, owner=True)
    asyncio.run(client.list_pending("alice"))
    assert client._token == "tok-2"
    with open(path) as fh:
        assert fh.read().strip() == "tok-2"


def test_non_owner_does_not_write_rotated_token(tmp_path):
    path = str(tmp_path / "tok")
    _write_token(path, "tok-1")
    conn = _conn([{"ok": True, "messages": [], "token": "tok-2"}])
    client = ms.BedMessageServiceClient(conn, token="tok-1", token_file=path, owner=False)
    asyncio.run(client.list_pending("alice"))
    assert client._token == "tok-2"
    with open(path) as fh:
        assert fh.read().strip() == "tok-1"


def test_non_owner_rereads_file_and_retries_once(tmp_path):
    path = str(tmp_path / "tok")
    _write_token(path, "tok-new")  # the owner rotated it since we read it
    conn = _conn([
        {"type": "error", "code": "not_authenticated"},
        {"ok": True, "messages": []},
    ])
    client = ms.BedMessageServiceClient(conn, token="tok-old", token_file=path, owner=False)
    reply = asyncio.run(client.list_pending("alice"))
    assert reply["ok"] is True
    assert conn.send.await_count == 2
    assert conn.send.await_args_list[1].args[0]["token"] == "tok-new"


def test_non_owner_no_retry_when_file_unchanged(tmp_path):
    path = str(tmp_path / "tok")
    _write_token(path, "tok-same")
    conn = _conn([{"type": "error", "code": "not_authenticated"}])
    client = ms.BedMessageServiceClient(conn, token="tok-same", token_file=path, owner=False)
    reply = asyncio.run(client.list_pending("alice"))
    assert reply["code"] == "not_authenticated"
    assert conn.send.await_count == 1


def test_owner_does_not_retry_on_not_authenticated(tmp_path):
    path = str(tmp_path / "tok")
    conn = _conn([{"type": "error", "code": "not_authenticated"}])
    client = ms.BedMessageServiceClient(conn, token="tok", token_file=path, owner=True)
    reply = asyncio.run(client.list_pending("alice"))
    assert reply["code"] == "not_authenticated"
    assert conn.send.await_count == 1
