"""Shared base for per-project message-family clients."""

from __future__ import annotations

from typing import Any, Dict

from bed.client.connection import BedConnection
from bed.client.exceptions import BedUnavailable
from bed.client.singleton import get_bed_connection


class BedMessageClient:
    """Base class for message-family clients (bank, player, table, ...).

    Subclasses hold a :class:`BedConnection` and implement each wire
    method as one line: ``return await self._request({"type": "...",
    ...})`` (plus a default, when ``not_found`` is set). The base
    handles request_id injection (via the connection), reply matching,
    and translation of ``error`` envelopes to :class:`BedUnavailable`.
    """

    _NO_DEFAULT = object()

    def __init__(self, args: Any) -> None:
        self._conn: BedConnection = get_bed_connection(args)

    async def _request(
        self,
        message: Dict[str, Any],
        *,
        not_found=(),
        default: Any = _NO_DEFAULT,
    ) -> Any:
        """Send ``message``; raise :class:`BedUnavailable` on errors.

        If the server's ``code`` is in ``not_found``:

        - If the caller passed ``default=``, return that value.
        - Otherwise return ``None``.

        Any other error code raises :class:`BedUnavailable`. The
        ``not_found`` mechanism is for "look up a thing; missing is
        a valid outcome" semantics (e.g. ``bank_history`` for a
        non-existent moniker). The transport-level failure (no
        connection, timeout) still raises :class:`BedUnavailable`
        regardless of ``not_found``.
        """
        reply = await self._conn.send(message)
        if reply.get("type") == "error":
            code = reply.get("code", "")
            if code in not_found:
                if default is self._NO_DEFAULT:
                    return None
                return default
            raise BedUnavailable(
                f"{message.get('type', '?')}: {code}: {reply.get('message')}"
            )
        return reply
