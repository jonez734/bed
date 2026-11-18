"""The AuthService the seam login runs against, for the front-end tools (``bed auth`` and every ``authenticate`` caller).

A front end logs in against the running bed's member store, through the same credential provider and token store the
server uses. This module is the one place that builds that service for a tool.
"""

from __future__ import annotations


def login_service():
    """Build the AuthService a front-end tool logs in with. Raises if bed's credential store cannot be built."""
    from bed.api.auth import AuthService
    from bed.api.credential_provider import get_provider
    from bed.api.session import SessionRegistry
    from bed.api.token_store import InMemoryTokenStore
    import argparse
    import secrets

    return AuthService(
        args=argparse.Namespace(debug=False, pool=None),
        session_registry=SessionRegistry(),
        token_store=InMemoryTokenStore(),
        credential_provider=get_provider("password"),
        secret=secrets.token_bytes(32),
        instance_id="tool",
        ttl_seconds=900,
        clock=None,
    )
