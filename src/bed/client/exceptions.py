"""Exceptions raised by the bed client."""


class BedUnavailable(Exception):
    """Raised when the bed WebSocket is unreachable or bed is unresponsive.

    Callers (e.g. message-family clients) translate this to a
    recoverable error envelope or a local-DB fallback.
    """
