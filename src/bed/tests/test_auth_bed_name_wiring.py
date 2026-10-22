"""The daemon hands its bed name to AuthService, so the login reply carries it.

AuthService receives database args, which do not hold the bed name. The daemon passes the
name explicitly, and the login reply uses it.
"""

import inspect
import importlib

import pytest

from bed.api import auth as auth_mod


@pytest.mark.unit
def test_auth_service_accepts_a_bed_name():
    sig = inspect.signature(auth_mod.AuthService.__init__)
    assert "bed_name" in sig.parameters


@pytest.mark.unit
def test_login_reply_uses_the_auth_services_bed_name():
    src = inspect.getsource(auth_mod.AuthService._handle_auth)
    assert "self.bed_name" in src
    assert 'getattr(self.args, "bed_name"' not in src


@pytest.mark.unit
def test_daemon_passes_its_bed_name_to_auth_service():
    main = importlib.import_module("bed.main")
    src = inspect.getsource(main)
    idx = src.index("self.auth_service = AuthService(")
    assert "bed_name=" in src[idx: idx + 400]
