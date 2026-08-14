"""Pytest fixtures for bed's auth tests.

The importable helpers (:class:`StubCredentialProvider`,
:class:`BedServerContext`, :func:`_start_bed_with_auth`, etc.) live
in the sibling module :mod:`_auth_helpers` because pytest's package
import mode (the test directory has ``__init__.py``) does not make
``conftest`` importable from test files. Pytest fixtures stay here
so they can be injected into test functions that opt in.

Mirrors the cross-suite convention used by
``test_bank_integration.py`` (auth instance_id
``"auth-tool-integration-test"``, ephemeral-port bind trick,
stub credential provider).
"""

from __future__ import annotations

import os
import sys


# Make ``bed.*`` and ``_auth_helpers`` importable when this
# conftest is collected by pytest from anywhere on disk.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _THIS_DIR)
sys.path.insert(0, "/home/opencode/data/work/bed/src")


import pytest

from bed.tests._auth_helpers import (
    LIVE_HOST,
    LIVE_PORT,
    StubCredentialProvider,
    _live_daemon_reachable,
)


@pytest.fixture
def stub_credential_provider() -> StubCredentialProvider:
    """Fresh :class:`StubCredentialProvider` per test."""
    return StubCredentialProvider()


@pytest.fixture
def live_daemon_reachable() -> bool:
    """True iff a live bed daemon is up on the local dev port."""
    return _live_daemon_reachable()


@pytest.fixture
def live_host() -> str:
    """Host string for the optional live-daemon test class."""
    return LIVE_HOST


@pytest.fixture
def live_port() -> int:
    """Port int for the optional live-daemon test class."""
    return LIVE_PORT
