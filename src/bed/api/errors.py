# bed/api/errors.py
# Error envelopes and token-scrubbing helpers for bed.

from __future__ import annotations

from typing import Any


CODE_TOKEN_EXPIRED = "token_expired"
CODE_TOKEN_INVALID = "token_invalid"
CODE_TOKEN_REVOKED = "token_revoked"
CODE_INSTANCE_MISMATCH = "bed_instance_mismatch"
CODE_NOT_AUTHENTICATED = "not_authenticated"
CODE_BAD_CREDENTIALS = "bad_credentials"
CODE_BED_SECRET_INSECURE = "bed_secret_insecure"
CODE_MISSING_CREDENTIALS = "missing_credentials"
CODE_DATABASE_ERROR = "database_error"
CODE_FORBIDDEN = "forbidden"


def error_envelope(code: str, message: str, *, recoverable: bool = False) -> dict:
    """Build a standard error envelope dict for the JSON wire protocol.

    The `recoverable` flag is the client's hint: True means the client may
    try a `reconnect` with its last good token, False means it must fall
    back to a fresh `auth` (interactive password prompt or headless fail).
    """
    return {
        "type": "error",
        "code": code,
        "message": message,
        "recoverable": recoverable,
    }


def not_authenticated() -> dict:
    return error_envelope(
        CODE_NOT_AUTHENTICATED,
        "Authentication required",
        recoverable=True,
    )


def forbidden(message: str = "Operation not permitted for this account") -> dict:
    """Build a forbidden envelope for ownership / authorization failures."""
    return error_envelope(CODE_FORBIDDEN, message, recoverable=False)


_TOKEN_KEYS = frozenset({"token", "tokens", "old_token", "new_token"})


def scrub_token(obj: Any) -> Any:
    """Recursively replace token values with a redaction marker.

    Walks dicts, lists, and tuples. Used by every debug/log site that
    may pass through a message containing a token. Strings are not
    parsed for token-shaped substrings; we only redact values whose
    key is in _TOKEN_KEYS. This is deliberately conservative: a stray
    free-form token in a log message is not our problem; the wire
    protocol puts the token in well-known fields, and those are the
    fields we scrub.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _TOKEN_KEYS and isinstance(v, str):
                out[k] = "<redacted>"
            else:
                out[k] = scrub_token(v)
        return out
    if isinstance(obj, list):
        return [scrub_token(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_token(v) for v in obj)
    return obj
