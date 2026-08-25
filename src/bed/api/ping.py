# bed/api/ping.py
# PingService: replies to ``ping`` with the bed instance's identity
# (name + version), so a probe client can verify it is talking to the
# expected daemon without an ``auth`` round-trip.
#
# ``BED.start()`` registers this service LAST so it always wins over
# any ``ping`` handler the loaded router registered first. bbsengine6
# emits a WARNING on the overwrite (see
# ``py/src/bbsengine6/net/transport.py:register_service``) so the
# swap is visible in the log.
#
# Wire shape::
#
#     C -> S  {"type": "ping"}
#     S -> C  {"type": "pong",
#              "name": "<bed_name>",
#              "version": "<bed.__version__>",
#              "timestamp": <server utcnow, ISO-8601>}

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bbsengine6.session import SessionManager

from .handler import BaseService


class PingService(BaseService):
    """Reply to ``ping`` with ``pong`` + bed name + version.

    The ``name`` is the per-instance bed name set via ``--bed-name`` or
    ``bed.name`` in bed.json. The ``version`` is :data:`bed.__version__`
    (the wheel's ``_version.py`` datestamp/githash). The ``timestamp``
    is the server's UTC time at the moment the pong is constructed, so
    the wire reply always carries an accurate, parseable ISO-8601
    timestamp regardless of whether the client included one in the
    request (which the binary ``PongPacket`` flow does for RTT, but
    the JSON ``*-ping`` shims (``bedping`` / ``casino-ping`` /
    ``zoid6-ping``) do not).
    """

    HANDLED_TYPES = ("ping",)

    def __init__(
        self,
        args: Any,
        session_manager: SessionManager,
        name: str,
    ) -> None:
        super().__init__(args, session_manager)
        self.name = str(name) if name else "bed"

    def register_all(self, server: Any) -> None:
        server.register_service(self, list(self.HANDLED_TYPES))

    async def handle_message(
        self,
        server: Any,
        websocket: Any,
        path: str,
        message: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if message.get("type") != "ping":
            return None
        from bed._version import __version__

        return {
            "type": "pong",
            "name": self.name,
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
