"""Bed client package.

Client-side WebSocket transport for talking to a bed daemon. Lives in
the bed project so the protocol client and server evolve together; both
empyre and casino import from here.
"""

from bed.client.bank import BedBankClient
from bed.client.connection import (
    BedConnection,
    _RequestId,
    _expected_result_type,
)
from bed.client.exceptions import BedUnavailable
from bed.client.messages import BedMessageClient
from bed.client.probe import probe_bed
from bed.client.singleton import (
    _CONNECTION_SINGLETON,
    _CONNECTION_SINGLETON_LOCK,
    get_bed_connection,
    reset_bed_connection,
)

__all__ = [
    "BedBankClient",
    "BedConnection",
    "BedMessageClient",
    "BedUnavailable",
    "get_bed_connection",
    "probe_bed",
    "reset_bed_connection",
]
