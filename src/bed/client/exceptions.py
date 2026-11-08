"""Exceptions raised by the bed client."""


class BedUnavailable(Exception):
    """Raised when the bed WebSocket is unreachable or bed is unresponsive.

    Callers (e.g. message-family clients) translate this to a
    recoverable error envelope or a local-DB fallback.

    ``code`` is the server's error code when bed refused the request (for example
    ``token_revoked`` for an expired or unknown token). It is ``None`` when the cause was
    transport-level (bed unreachable, timed out). Callers can tell a refused token from a dead
    daemon by ``code``, without parsing the message.
    """

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message)
        self.code = code
