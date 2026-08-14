"""Bed client package.

Client-side WebSocket transport for talking to a bed daemon. Lives in
the bed project so the protocol client and server evolve together; both
empyre and casino import from here.
"""

from bed.client.authservice import (
    BedAuthServiceClient,
    get_auth_client,
    reset_auth_client,
)
from bed.client.bank import BedBankClient
from bed.client.bankservice import (
    BedBankServiceClient,
    get_bank_client,
    reset_bank_client,
)
from bed.client.connection import BedConnection
from bed.client.exceptions import BedUnavailable
from bed.client.messages import BedMessageClient
from bed.client.messageservice import (
    BedMessageServiceClient,
    get_message_client,
    reset_message_client,
)
from bed.client.probe import probe_bed
from bed.client.singleton import (
    get_bed_connection,
    reset_bed_connection,
)

__all__ = [
    "BedAuthServiceClient",
    "BedBankClient",
    "BedBankServiceClient",
    "BedConnection",
    "BedMessageClient",
    "BedMessageServiceClient",
    "BedUnavailable",
    "get_auth_client",
    "get_bank_client",
    "get_bed_connection",
    "get_message_client",
    "probe_bed",
    "reset_auth_client",
    "reset_bank_client",
    "reset_bed_connection",
    "reset_message_client",
] 
