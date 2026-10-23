"""BedBankClient and the other message-family clients use the connection they are given.

BedMessageClient used to take `args` only and always resolved its own connection through the
singleton. Given a BedConnection, it silently replaced it with a different one, so the socket it
dialed was never the one the caller closed. A connection passed in must be the one used.
"""

import pytest

from bed.client.bank import BedBankClient
from bed.client.connection import BedConnection
from bed.client.messages import BedMessageClient


class _Args:
    bed_host = "127.0.0.1"
    bed_port = 8765
    bed_path = "/"
    bed_call_timeout = 5.0


@pytest.mark.unit
def test_message_client_keeps_a_connection_it_is_given():
    conn = BedConnection(_Args())
    client = BedMessageClient(conn)
    assert client._conn is conn


@pytest.mark.unit
def test_bank_client_keeps_a_connection_it_is_given():
    conn = BedConnection(_Args())
    assert BedBankClient(conn)._conn is conn


@pytest.mark.unit
def test_message_client_still_resolves_a_connection_from_args():
    client = BedMessageClient(_Args())
    assert isinstance(client._conn, BedConnection)
